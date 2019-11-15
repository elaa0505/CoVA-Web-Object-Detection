import numpy as np
import os
from PIL import Image
import torch
from torch.utils.data import DataLoader
import torchvision
from torchvision import transforms

from utils import pkl_load


class WebDataset(torchvision.datasets.VisionDataset):
    """
    Class to load train/val/test datasets
    """
    def __init__(self, root, img_ids, max_bg_boxes=-1):
        """
        Args:
            root: directory where data is located
                Must contain x.png Image and corresponding x.pkl BBox coordinates file
            img_ids: list of img_names to consider
            max_bg_boxes: randomly sample this many number of background boxes (class 0) while training (default: -1 --> no sampling, take all)
                All samples of class > 0 are always taken
                NOTE: For val and test data, max_bg_boxes SHOULD be -1 (no sampling)
        """
        super(WebDataset, self).__init__(root)
        
        self.ids = img_ids
        self.img_transform = transforms.ToTensor()
        self.max_bg_boxes = max_bg_boxes
        ## convert to 0 MEAN, 1 VAR ???
    
    def __getitem__(self, index):
        """
        Args:
            index (int): Index in range [0, self.__len__ - 1]

        Returns:
            image: torch.Tensor of size [3,H,W].
            bboxes: torch.Tensor of size [n_bbox, 4] i.e. n bboxes each of [top_left_x, top_left_y, bottom_right_x, bottom_right_y]
            labels: torch.Tensor of size [n_bbox] i.e. each value is label of the corresponding bbox
        """
        img_id = self.ids[index]
        
        img = Image.open('%s/%s.png' % (self.root, img_id)).convert('RGB')
        img = self.img_transform(img)
        
        input_boxes = pkl_load('%s/%s.pkl' % (self.root, img_id))
        bg_boxes = input_boxes['other_boxes']

        if self.max_bg_boxes > 0:
            np.random.shuffle(bg_boxes)
            bg_boxes = bg_boxes[:self.max_bg_boxes]

        bboxes = torch.Tensor( np.concatenate((input_boxes['gt_boxes'], bg_boxes), axis=0) )
        bboxes[:,2:] += bboxes[:,:2]
        
        labels = torch.Tensor([1,2,3] + [0]*len(bg_boxes)).long()

        return img, bboxes, labels

    def __len__(self):
        return len(self.ids)

########################## End of class `WebDataset` ##########################


def custom_collate_fn(batch):
    """
    Since all images might have different number of BBoxes, to use batch_size > 1,
    custom collate_fn has to be created that creates a batch
    
    Args:
        batch: list of N=`batch_size` tuples. Example [(img_1, bboxes_1, labels_1), ..., (img_N, bboxes_N, labels_N)]
    
    Returns:
        batch: contains images, bboxes, labels
            images: torch.Tensor [N, 3, img_H, img_W]
            bboxes: torch.Tensor [total_n_bboxes_in_batch, 5]
                each each of [batch_img_index, top_left_x, top_left_y, bottom_right_x, bottom_right_y]
            labels: torch.Tensor [total_n_bboxes_in_batch]
    """
    images, bboxes, labels = zip(*batch)
    # images = (img_1, ..., img_N) each element of size [3, img_H, img_W]
    # bboxes = (bboxes_1, ..., bboxes_N) each element of size [n_bboxes_in_image, 4]
    # labels = (labels_1, ..., labels_N) each element of size [n_bboxes_in_image]
    
    images = torch.stack(images, 0)
    
    bboxes_with_batch_index = []
    for i, bbox in enumerate(bboxes):
        batch_indices = torch.Tensor([i]*bbox.size()[0]).view(-1,1)
        bboxes_with_batch_index.append(torch.cat((batch_indices, bbox), dim=1))
    bboxes_with_batch_index = torch.cat(bboxes_with_batch_index)
    
    labels = torch.cat(labels)
    
    return images, bboxes_with_batch_index, labels


def load_data(data_dir, train_img_ids, val_img_ids, test_img_ids, batch_size, num_workers=4, max_bg_boxes=-1):
    """
    Args:
        data_dir: directory which contains x.png Image and corresponding x.pkl BBox coordinates file
        train_img_ids: list of img_names to consider in train split
        val_img_ids: list of img_names to consider in val split
        test_img_ids: list of img_names to consider in test split
        batch_size: size of batch in train_loader
        max_bg_boxes: randomly sample this many number of background boxes (class 0) while training (default: -1 --> no sampling, take all)
            All samples of class > 0 are always taken
            NOTE: For val and test data, max_bg_boxes SHOULD be -1 (no sampling)
    
    Returns:
        train_loader, val_loader, test_loader (torch.utils.data.DataLoader)
    """
    assert np.intersect1d(train_img_ids, val_img_ids).size == 0
    assert np.intersect1d(val_img_ids, test_img_ids).size == 0
    assert np.intersect1d(train_img_ids, test_img_ids).size == 0
    
    train_dataset = WebDataset(data_dir, train_img_ids, max_bg_boxes)
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=num_workers,
                              collate_fn=custom_collate_fn, drop_last=False)

    val_dataset = WebDataset(data_dir, val_img_ids, max_bg_boxes=-1)
    val_loader = DataLoader(val_dataset, batch_size=1, shuffle=False, num_workers=num_workers,
                            collate_fn=custom_collate_fn, drop_last=False)
    
    test_dataset = WebDataset(data_dir, test_img_ids, max_bg_boxes=-1)
    test_loader = DataLoader(test_dataset, batch_size=1, shuffle=False, num_workers=num_workers,
                             collate_fn=custom_collate_fn, drop_last=False)
    
    print('---> No. of Images\t Train: %d\t Val: %d\t Test: %d\n' % ( len(train_dataset), len(val_dataset), len(test_dataset) ))
    
    return train_loader, val_loader, test_loader