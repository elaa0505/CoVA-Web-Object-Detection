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
    def __init__(self, root, img_ids):
        """
        Args:
            root: directory where data is located
                Must contain x.png Image and corresponding x.pkl BBox coordinates file
            img_ids: list of img_names to consider
        """
        super(WebDataset, self).__init__(root)
        
        self.ids = img_ids
        self.img_transform = transforms.ToTensor()
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
        bboxes = torch.Tensor( np.concatenate((input_boxes['gt_boxes'], input_boxes['other_boxes']), axis=0) )
        bboxes[:,2:] += bboxes[:,:2]
        
        labels = torch.Tensor([1,2,3] + [0]*input_boxes['other_boxes'].shape[0]).long()

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
                each each of [bath_img_index, top_left_x, top_left_y, bottom_right_x, bottom_right_y]
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


def load_data(data_dir, train_img_ids, val_img_ids, test_img_ids, batch_size, num_workers=4):
    """
    Args:
        data_dir: directory which contains x.png Image and corresponding x.pkl BBox coordinates file
        train_img_ids: list of img_names to consider in train split
        val_img_ids: list of img_names to consider in val split
        test_img_ids: list of img_names to consider in test split
        batch_size: size of batch in train_loader
    
    Returns:
        train_loader, val_loader, test_loader (torch.utils.data.DataLoader)
    """
    assert np.intersect1d(train_img_ids, val_img_ids).size == 0
    assert np.intersect1d(val_img_ids, test_img_ids).size == 0
    assert np.intersect1d(train_img_ids, test_img_ids).size == 0
    
    train_dataset = WebDataset(data_dir, train_img_ids)
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=num_workers,
                              collate_fn=custom_collate_fn, drop_last=False)

    val_dataset = WebDataset(data_dir, val_img_ids)
    val_loader = DataLoader(val_dataset, batch_size=1, shuffle=False, num_workers=num_workers,
                            collate_fn=custom_collate_fn, drop_last=False)
    
    test_dataset = WebDataset(data_dir, test_img_ids)
    test_loader = DataLoader(test_dataset, batch_size=1, shuffle=False, num_workers=num_workers,
                             collate_fn=custom_collate_fn, drop_last=False)
    
    print('Train Images:', len(train_dataset))
    print('Val Images:', len(val_dataset))
    print('Test  Images:', len(test_dataset))
    
    return train_loader, val_loader, test_loader