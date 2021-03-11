import numpy as np
import torch
import torch.nn as nn
import torchvision

from utils import count_parameters


class VAMWOD(nn.Module):
    def __init__(self, roi_output_size, img_H, n_classes, use_context=True, use_attention=True, hidden_dim=384, 
                 use_bbox_feat=True, bbox_hidden_dim=32, n_additional_feat=0, drop_prob=0.2, class_names=None):
        """
        Implementation of our Visual Attention-based Model for Webpage Object Detection (VAMWOD)

        roi_output_size: Tuple (int, int) which will be output of the roi_pool layer for each channel of convnet_feature
        img_H: height of image given as input to the convnet. Image assumed to be of same W and H
        n_classes: num of classes for BBoxes
        use_context: if True, use context for context_representation along with h_i (default: True) 
        use_attention: if True, learn scores for all n_context contexts and take weighted avg for context_representation
            NOTE: this parameter is not used if use_context=False
        hidden_dim: size of hidden contextual representation, used when use_attention=True (default: 384)
        use_bbox_feat: if True, then use [x,y,w,h,asp_ratio] with convnet visual features (default: True)
        bbox_hidden_dim: size of hidden representation of 5 bbox features, used when use_bbox_feat=True (default: 32)
        n_additional_feat: num of additional features for each bbox to be used along with visual and bbox features
        drop_prob: dropout probability (default: 0.2)
        class_names: list of n_classes string elements containing names of the classes (default: [0, 1, ..., n_classes-1])
        """
        super(VAMWOD, self).__init__()

        self.n_classes = n_classes
        self.use_context = use_context
        self.use_attention = use_attention
        self.use_bbox_feat = use_bbox_feat
        self.bbox_hidden_dim = bbox_hidden_dim
        self.n_additional_feat = n_additional_feat
        self.class_names = np.arange(self.n_classes).astype(str) if class_names is None else class_names

        ##### REPRESENTATION NETWORK (RN) #####
        self.convnet = torchvision.models.resnet18(pretrained=True)
        modules = list(self.convnet.children())[:-5] # remove last few layers!
        self.convnet = nn.Sequential(*modules)

        _imgs = torch.autograd.Variable(torch.Tensor(1, 3, img_H, img_H))
        _conv_feat = self.convnet(_imgs)
        _convnet_output_size = _conv_feat.shape # [1, C, H, W]
        spatial_scale = _convnet_output_size[2]/img_H

        self.roi_pool = torchvision.ops.RoIPool(roi_output_size, spatial_scale)

        self.n_visual_feat = _convnet_output_size[1] * roi_output_size[0] * roi_output_size[1]
        self.n_bbox_feat = self.bbox_hidden_dim if self.use_bbox_feat else 0 # [x,y,w,h,asp_rat] of BBox encoded to n_bbox_feat
        self.n_feat = self.n_visual_feat + self.n_bbox_feat + self.n_additional_feat

        if self.use_bbox_feat:
            self.bbox_feat_encoder = nn.Sequential(
                nn.Linear(5, self.n_bbox_feat),
                nn.BatchNorm1d(self.n_bbox_feat),
                nn.ReLU(),
            )
        
        if self.n_additional_feat > 0:
            self.bn_additional_feat = nn.BatchNorm1d(self.n_additional_feat)
        else:
            self.bn_additional_feat = lambda x: x

        ##### CONTEXT ATTENTIVE NETWORK (CAN) #####
        if self.use_context and self.use_attention:
            self.gat = GraphAttentionLayer(self.n_feat, hidden_dim)
        
        ##### FC LAYERS #####
        self.n_total_feat = self.n_feat + hidden_dim
        self.decoder = nn.Sequential(
            nn.Dropout(drop_prob),
            nn.Linear(self.n_total_feat, self.n_total_feat),
            nn.BatchNorm1d(self.n_total_feat),
            nn.ReLU(),
            nn.Dropout(drop_prob),
            nn.Linear(self.n_total_feat, self.n_classes),
        )

        print('Model Parameters:', count_parameters(self))
    
    def forward(self, images, bboxes, additional_feats, context_indices):
        """
        images: torch.Tensor of size [batch_size, 3, img_H, img_H]
        bboxes: torch.Tensor [N, 5], N = total_n_bboxes_in_batch
            each of [batch_img_index, top_left_x, top_left_y, bottom_right_x, bottom_right_y]
        additional_feats: torch.Tensor [N, n_additional_feat]
        context_indices: Torch.LongTensor [N, n_context]
            indices (0 to N-1) of `n_context` bboxes that are in context for a given bbox. If not enough found, rest are -1
        
        Returns:
            prediction_scores: torch.Tensor of size [N, n_classes]
        """
        N = bboxes.shape[0]

        ##### BBOX FEATURES #####
        if self.use_bbox_feat:
            bbox_features = bboxes[:, 1:].clone() # discard batch_img_index column
            bbox_features[:, 2:] -= bbox_features[:, :2] # convert to [top_left_x, top_left_y, width, height]
            
            bbox_asp_ratio = (bbox_features[:, 2]/bbox_features[:, 3]).view(N, 1)
            bbox_features = torch.cat((bbox_features, bbox_asp_ratio), dim=1)
            
            bbox_features = self.bbox_feat_encoder(bbox_features)
        else:
            bbox_features = bboxes[:, :0] # size [n_bboxes, 0]
        
        ##### OWN VISUAL + BBOX FEATURES + ADDITIONAL FEATURES #####
        own_features = self.roi_pool(self.convnet(images), bboxes).view(N, self.n_visual_feat)
        additional_feats = self.bn_additional_feat(additional_feats)
        own_features = torch.cat((own_features, bbox_features, additional_feats), dim=1)

        ##### CONTEXT FEATURES USING SELF-ATTENTION #####
        if self.use_context:
            if self.use_attention:
                context_representation = self.gat(own_features, context_indices)
            else: # average of context features for context representation [N, n_feat]
                n_context = context_indices.shape[1]
                zero_feat = torch.zeros((1, self.n_feat)).to(images.device) # for -1 contexts i.e. extra padded
                own_feat_padded = torch.cat((own_features, zero_feat), dim=0)
                h_j = own_feat_padded[context_indices.view(-1)].view(N, n_context, self.n_feat) # context_features
                context_representation = h_j.sum(dim=1) / (context_indices != -1).sum(dim=1).view(N, 1)
        else:
            context_representation = own_features[:, :0] # size [n_bboxes, 0]

        ##### FINAL FEATURE VECTOR #####
        combined_feat = torch.cat((own_features, context_representation), dim=1)
        output = self.decoder(combined_feat)

        return output


class GraphAttentionLayer(nn.Module):
    """
    Simple GAT layer, similar to https://arxiv.org/abs/1710.10903
    """
    def __init__(self, in_features, hidden_dim, alpha=0.2):
        super(GraphAttentionLayer, self).__init__()
        self.in_features = in_features
        self.hidden_dim = hidden_dim

        self.W_i = nn.Linear(self.in_features, self.hidden_dim, bias=False)
        self.W_j = nn.Linear(self.in_features, self.hidden_dim, bias=False)
        # nn.init.xavier_uniform_(self.W_i.weight, gain=1.414)
        # nn.init.xavier_uniform_(self.W_j.weight, gain=1.414)
        
        self.attention_layer = nn.Linear(2*self.hidden_dim, 1)
        # with torch.no_grad():
        #     self.attention_layer.weight.fill_(0)

        self.leakyrelu = nn.LeakyReLU(alpha)

        # self.context_encoder = nn.Sequential(
        #     nn.Linear(self.n_feat, self.n_feat),
        #     nn.BatchNorm1d(self.n_feat),
        #     nn.ReLU(),
        # )

    def forward(self, h_i, context_indices):
        """
        h_i: features for all bboxes torch.Tensor of shape [N, in_features]
        context_indices: Torch.LongTensor [N, n_context]
            indices (0 to N-1) of `n_context` bboxes that are in context for a given bbox. If not enough found, rest are -1
        """
        N, n_context = context_indices.shape

        zero_feat = torch.zeros((1, self.in_features)).to(h_i.device) # to map -1 contexts to zero_feat
        h_i_padded = torch.cat((h_i, zero_feat), dim=0)
        h_j = h_i_padded[context_indices.view(-1)].view(N, n_context, self.in_features) # context_features

        Wh_i = self.W_i(h_i) # [N, hidden_dim]
        Wh_i_repeated = Wh_i.repeat_interleave(n_context, dim=0).view(N, n_context, self.hidden_dim)

        Wh_j = self.W_j(h_j) # [N, n_context, hidden_dim]

        attention_wts = self.attention_layer(torch.cat((Wh_i_repeated, Wh_j), dim=2)).squeeze(2) # [N, n_context]
        attention_wts = self.leakyrelu(attention_wts)

        minus_inf = -9e15*torch.ones_like(attention_wts)
        attention_wts = torch.where(context_indices >= 0, attention_wts, minus_inf)
        attention_wts = torch.softmax(attention_wts, dim=1) # [N, n_context]
        
        h_prime = (attention_wts.unsqueeze(-1) * Wh_j).sum(1) # weighted avg of contexts [N, hidden_dim]
        # h_prime = self.context_encoder(context_representation) # [N, in_features]

        return h_prime