import math
import os
import random
import cv2

import time
import numpy as np
from PIL import Image
import PIL.ImageOps

import torchvision
import torchvision.datasets as datasets
import torchvision.transforms as transforms
from torch.utils.data import DataLoader, Dataset
import torchvision.utils
import torch
from torch.autograd import Variable
import torch.nn as nn
from torch import optim
import torch.nn.functional as F

from dirs import LABELLED_DIR
from utils.colors import RGB_GREEN
from utils.image import join_horizontal, scale, show
from .edge_detector import EdgeDetector
from .detector import Detector
from .stage import DetectionResult, Stage
from utils.stubs import CVImage

class SiameseNetworkDataset(Dataset):
    def __init__(self,imageFolderDataset,transform=None):
        self.imageFolderDataset = imageFolderDataset
        self.transform = transform

    def __getitem__(self,index):
        img0_tuple = random.choice(self.imageFolderDataset.imgs)

        #We need to approximately 50% of images to be in the same class
        should_get_same_class = random.randint(0,1)
        if should_get_same_class:
            while True:
                #Look untill the same class image is found
                img1_tuple = random.choice(self.imageFolderDataset.imgs)
                if img0_tuple[1] == img1_tuple[1]:
                    break
        else:

            while True:
                #Look untill a different class image is found
                img1_tuple = random.choice(self.imageFolderDataset.imgs)
                if img0_tuple[1] != img1_tuple[1]:
                    break

        img0 = Image.open(img0_tuple[0])
        img1 = Image.open(img1_tuple[0])
        img0 = PIL.ImageOps.expand(img0, border=int(random.uniform(0, 30)), fill=0)

        img0 = img0.convert("L")
        img1 = img1.convert("L")


        if self.transform is not None:
            img0 = self.transform(img0)
            img1 = self.transform(img1)

        return img0, img1, torch.from_numpy(np.array([int(img1_tuple[1] != img0_tuple[1])], dtype=np.float32))

    def __len__(self):
        return len(self.imageFolderDataset.imgs)

#create the Siamese Neural Network
class SiameseNetwork(nn.Module):

    def __init__(self):
        super(SiameseNetwork, self).__init__()

        # Setting up the Sequential of CNN Layers
        self.cnn1 = nn.Sequential(
            nn.Conv2d(1, 96, kernel_size=11,stride=4),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(3, stride=2),

            nn.Conv2d(96, 256, kernel_size=5, stride=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2, stride=2),

            nn.Conv2d(256, 384, kernel_size=3,stride=1),
            nn.ReLU(inplace=True)
        )

        # Setting up the Fully Connected Layers
        self.fc1 = nn.Sequential(
            nn.Linear(384, 1024),
            nn.ReLU(inplace=True),

            nn.Linear(1024, 256),
            nn.ReLU(inplace=True),

            nn.Linear(256,2)
        )

    def forward_once(self, x):
        print(f"{time.time():.4f}", "in forward once")
        # This function will be called for both images
        # It's output is used to determine the similiarity
        output = self.cnn1(x)
        output = output.view(output.size()[0], -1)
        output = self.fc1(output)
        print(f"{time.time():.4f}", "done with forward once")
        return output

    def forward(self, input1, input2):
        # In this function we pass in both images and obtain both vectors
        # which are returned
        output1 = self.forward_once(input1)
        output2 = self.forward_once(input2)

        return output1, output2

# Define the Contrastive Loss Function
class ContrastiveLoss(torch.nn.Module):
    def __init__(self, margin=2.0):
        super(ContrastiveLoss, self).__init__()
        self.margin = margin

    def forward(self, output1, output2, label):
      # Calculate the euclidian distance and calculate the contrastive loss
      euclidean_distance = F.pairwise_distance(output1, output2, keepdim = True)

      loss_contrastive = torch.mean((1-label) * torch.pow(euclidean_distance, 2) +
                                    (label) * torch.pow(torch.clamp(self.margin - euclidean_distance, min=0.0), 2))


      return loss_contrastive


class SiameseDetector(Detector):

    def __init__(self):
        self.net = SiameseNetwork()
        self.train()
        super().__init__()

    def train(self):
        # Load the training dataset
        folder_dataset = datasets.ImageFolder(root="./recognition/images/data/")

        # Resize the images and transform to tensors
        transformation = transforms.Compose([transforms.Resize((100,100)),
                                             transforms.ToTensor()
                                            ])

        # Initialize the network
        siamese_dataset = SiameseNetworkDataset(imageFolderDataset=folder_dataset,
                                                transform=transformation)
        train_dataloader = DataLoader(siamese_dataset,
                                shuffle=True,
                                num_workers=8,
                                batch_size=64)
        net = self.net
        criterion = ContrastiveLoss()
        optimizer = optim.Adam(net.parameters(), lr = 0.0005 )

        counter = []
        loss_history = []
        iteration_number= 0

        # Iterate throught the epochs
        for epoch in range(10):

            # Iterate over batches
            for i, (img0, img1, label) in enumerate(train_dataloader, 0):


                # # Send the images and labels to CUDA
                # img0, img1, label = img0.cuda(), img1.cuda(), label.cuda()

                # Zero the gradients
                optimizer.zero_grad()

                # Pass in the two images into the network and obtain two outputs
                output1, output2 = net(img0, img1)

                # Pass the outputs of the networks and label into the loss function
                loss_contrastive = criterion(output1, output2, label)

                # Calculate the backpropagation
                loss_contrastive.backward()

                # Optimize
                optimizer.step()

                # Every 10 batches print out the loss
                if i % 10 == 0 :
                    print(f"Epoch number {epoch}\n Current loss {loss_contrastive.item()}\n")
                    iteration_number += 10

                    counter.append(iteration_number)
                    loss_history.append(loss_contrastive.item())


    def preprocess_image(self, img: CVImage) -> CVImage:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img


    def detect(self, image: CVImage) -> Stage[DetectionResult]:
        edge_detector = EdgeDetector()
        stage = edge_detector.detect(image)
        rects = stage.result

        output: DetectionResult = []

        for rect in rects:
            x,y,w,h = rect
            tile_img = image[y:y+h, x:x+w]
            tile_img = self.preprocess_image(tile_img)
            # tile_img = self.crop_image(tile_img, self.detect_corners(tile_img))

            labels = [basename.split('.')[0] for basename in os.listdir(LABELLED_DIR)]

            best_label = "None"
            best_score = 0
            for label in labels:
                path = os.path.join(LABELLED_DIR, f"{label}.png")
                assert os.path.exists(path), path
                target_img = cv2.imread(path)
                target_img = cv2.cvtColor(target_img, cv2.COLOR_BGR2RGB)
                target_img = self.preprocess_image(target_img)

                score = self.compare_and_score(tile_img, target_img)

                side_by_side = join_horizontal([tile_img, target_img])
                # cv2.putText(side_by_side, f"{score:.2f}", (80, 100), fontFace=1, fontScale=3, color=(0,0,0), thickness=5)
                # show(side_by_side)
                if score > best_score:
                    best_label = label
                    best_score = score
            output.append((rect, best_label))

        def display():
            canvas = image.copy()
            for rect, label in output:
                x,y,w,h = rect
                cv2.rectangle(canvas, (x,y), (x+w, y+h), RGB_GREEN)

                cv2.putText(canvas, label,
                    org=(int(x + 0.1 * w),int(y + 0.2 * h)),
                    fontFace=1,
                    fontScale=2,
                    color=RGB_GREEN,
                    thickness=2)
            return canvas
        return stage.next(output, display_callback=display)


    def compare_and_score(self, img1: CVImage, img2: CVImage) -> float:
        w1, h1, *_ = img1.shape
        w2, h2, *_ = img2.shape

        scale_factor = 1 / math.sqrt((w1 * h1) / (w2 * h2))
        img1 = scale(img1, scale_factor)
