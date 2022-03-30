import torch
from util import Averager, get_submission
from tqdm import tqdm
from map_boxes import mean_average_precision_for_boxes
categories = ["General trash", "Paper", "Paper pack", "Metal", 
              "Glass", "Plastic", "Styrofoam", "Plastic bag", "Battery", "Clothing"]


def train(args, model, optimizer, train_data_loader, valid_data_loader, gt, wandb):
    scaler = torch.cuda.amp.GradScaler()
    # scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=6, eta_min=0, verbose=False)
    device = 'cuda'
    loss_hist = Averager()
    model.cuda();

    for epoch in range(args['NUM_EPOCHS']):
        model.train()
        loss_hist.reset()

        for images, targets, image_ids in tqdm(train_data_loader):

                images = torch.stack(images) # bs, ch, w, h - 16, 3, 512, 512
                images = images.to(device).float()
                boxes = [target['boxes'].to(device).float() for target in targets]
                labels = [target['labels'].to(device).float() for target in targets]
                target = {"bbox": boxes, "cls": labels}

                loss, cls_loss, box_loss = model(images, target).values()

                loss_value = loss.detach().item()

                loss_hist.send(loss_value)

                optimizer.zero_grad()
                '''
                Automatic Mixed Precision(amp)
                '''
                scaler.scale(loss).backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 35) # grad clip
                scaler.step(optimizer)
                scaler.update()

        train_loss =  loss_hist.value     
        wandb.log({'train/loss': train_loss})
        
        with torch.no_grad():
            model.eval()
            outputs = []
            loss_hist.reset()
            print('Start Validation')
            for images, targets, image_ids in tqdm(valid_data_loader):
                images = torch.stack(images)
                images = images.to(device).float()
                boxes = [target['boxes'].to(device).float() for target in targets]
                labels = [target['labels'].to(device).float() for target in targets]

                target = {"bbox": boxes, "cls": labels}

                target["img_scale"] = torch.tensor([1.0] * args['BATCH_SIZE'], dtype=torch.float).to(device)
                target["img_size"] = torch.tensor([images[0].shape[-2:]] * args['BATCH_SIZE'], dtype=torch.float).to(device)

                result = model(images, target) 
                loss = result['loss']
                output = result['detections']
                loss_hist.send(loss.detach().item())
                # wandb.log({'val/cls_loss': result['class_loss'].detach().item(), 'val/box_loss': result['box_loss'].detach().item()})


                for out in output:
                    outputs.append({'boxes': out.detach().cpu().numpy()[:,:4], 
                                    'scores': out.detach().cpu().numpy()[:,4], 
                                    'labels': out.detach().cpu().numpy()[:,-1]})

        val_loss =  loss_hist.value     
        # scheduler.step()
        preds = get_submission(outputs, args['VAL_ANN'], score_threshold=0.1, valid=True)
        mean_ap, average_precisions = mean_average_precision_for_boxes(gt, preds, iou_threshold=0.5)
        wandb.log({'val/bbox_mAP_50': mean_ap})

        print("Epoch: {}/{}.. ".format(epoch+1, args['NUM_EPOCHS']) +
                "Training Loss: {:.4f}.. ".format(train_loss)+
                "Valid Loss: {:.4f}.. ".format(val_loss) + 
                "Valid mAP: {:.4f}.. ".format(mean_ap))

        print(f'{"CLASS_NAME":15s}| {"AP":5s} | COUNTS')
        print('ㅡ'*20)
        for _class_num in average_precisions:
            print(f'{categories[int(_class_num)]:15s}| {average_precisions[_class_num][0]:0.3f} | {average_precisions[_class_num][1]}')

        torch.save(model.state_dict(), f'pretrained/epoch_{epoch+1}.pth')