import datetime
import os

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2' 
import tensorflow as tf
import tensorflow.keras.backend as K
from tensorflow.keras.optimizers import SGD, Adam

from nets.frcnn import get_model
from nets.frcnn_training import (ProposalTargetCreator, classifier_cls_loss,
                                 classifier_smooth_l1, get_lr_scheduler,
                                 rpn_cls_loss, rpn_smooth_l1)
from utils.anchors import get_anchors
from utils.callbacks import LossHistory
from utils.dataloader import FRCNNDatasets, OrderedEnqueuer
from utils.utils import get_classes, show_config
from utils.utils_bbox import BBoxUtility
from utils.utils_fit import fit_one_epoch

if __name__ == "__main__":
   
    #----------------------------------------------------------------------------------------------------------------------------#
    model_path      = 'model_data/voc_weights_resnet.h5'
    #------------------------------------------------------#
    #   input_shape     输入的shape大小
    #------------------------------------------------------#
    input_shape     = [600, 600]
    #---------------------------------------------#
    #   vgg
    #   resnet50
    #---------------------------------------------#
    backbone        = "resnet50"
    #------------------------------------------------------------------------#
    #   anchors_size用于设定先验框的大小，每个特征点均存在9个先验框。
    #   anchors_size每个数对应3个先验框。
    #   当anchors_size = [8, 16, 32]的时候，生成的先验框宽高约为：
    #   [128, 128]; [256, 256] ; [512, 512]; [128, 256]; 
    #   [256, 512]; [512, 1024]; [256, 128]; [512, 256]; 
    #   [1024, 512]; 详情查看anchors.py
    #------------------------------------------------------------------------#
    anchors_size    = [128, 256, 512]
    #------------------------------------------------------------------#
    Init_Epoch          = 0
    Freeze_Epoch        = 50
    Freeze_batch_size   = 4
    #------------------------------------------------------------------#
    #   解冻阶段训练参数
    #------------------------------------------------------------------#
    UnFreeze_Epoch      = 100
    Unfreeze_batch_size = 2
    #------------------------------------------------------------------#
    #   Freeze_Train    是否进行冻结训练
    #                   默认先冻结主干训练后解冻训练。
    #------------------------------------------------------------------#
    Freeze_Train        = True
    
    #------------------------------------------------------------------#
    #   其它训练参数：学习率、优化器、学习率下降有关
    #------------------------------------------------------------------#
    #   Init_lr         模型的最大学习率
    #                   当使用Adam优化器时建议设置  Init_lr=1e-4
    #                   当使用SGD优化器时建议设置   Init_lr=1e-2
    #   Min_lr          模型的最小学习率，默认为最大学习率的0.01
    #------------------------------------------------------------------#
    Init_lr             = 1e-4
    Min_lr              = Init_lr * 0.01
    #------------------------------------------------------------------#
    #   optimizer_type  使用到的优化器种类，可选的有adam、sgd
    #                   当使用Adam优化器时建议设置  Init_lr=1e-4
    #                   当使用SGD优化器时建议设置   Init_lr=1e-2
    #   momentum        优化器内部使用到的momentum参数
    #------------------------------------------------------------------#
    optimizer_type      = "adam"
    momentum            = 0.9
    #------------------------------------------------------------------#
    #   lr_decay_type   使用到的学习率下降方式，可选的有'step'、'cos'
    #------------------------------------------------------------------#
    lr_decay_type       = 'cos'
    #------------------------------------------------------------------#
    #   save_period     多少个epoch保存一次权值
    #------------------------------------------------------------------#
    save_period         = 5
    #------------------------------------------------------------------#
    #   save_dir        权值与日志文件保存的文件夹
    #------------------------------------------------------------------#
    save_dir            = 'logs'
    #------------------------------------------------------------------#
    #   num_workers     用于设置是否使用多线程读取数据，1代表关闭多线程
    #------------------------------------------------------------------#
    num_workers         = 1

    #------------------------------------------------------#
    #   train_annotation_path   训练图片路径和标签
    #   val_annotation_path     验证图片路径和标签
    #------------------------------------------------------#
    train_annotation_path   = '2007_train.txt'
    val_annotation_path     = '2007_val.txt'

    #------------------------------------------------------#
    #   设置用到的显卡
    #------------------------------------------------------#
    os.environ["CUDA_VISIBLE_DEVICES"]  = ','.join(str(x) for x in train_gpu)
    ngpus_per_node                      = len(train_gpu)
    
    gpus = tf.config.experimental.list_physical_devices(device_type='GPU')
    for gpu in gpus:
        tf.config.experimental.set_memory_growth(gpu, True)

    if ngpus_per_node > 1:
        strategy = tf.distribute.MirroredStrategy()
    else:
        strategy = None
    print('Number of devices: {}'.format(ngpus_per_node))

    #----------------------------------------------------#
    #   获取classes和anchor
    #----------------------------------------------------#
    class_names, num_classes = get_classes(classes_path)
    num_classes += 1
    anchors = get_anchors(input_shape, backbone, anchors_size)

    #----------------------------------------------------#
    #   判断是否多GPU载入模型和预训练权重
    #----------------------------------------------------#
    if ngpus_per_node > 1:
        with strategy.scope():
            model_rpn, model_all = get_model(num_classes, backbone = backbone)
            if model_path != '':
                #------------------------------------------------------#
                #   载入预训练权重
                #------------------------------------------------------#
                print('Load weights {}.'.format(model_path))
                model_rpn.load_weights(model_path, by_name=True)
                model_all.load_weights(model_path, by_name=True)
    else:
        model_rpn, model_all = get_model(num_classes, backbone = backbone)
        if model_path != '':
            #------------------------------------------------------#
            #   载入预训练权重
            #------------------------------------------------------#
            print('Load weights {}.'.format(model_path))
            model_rpn.load_weights(model_path, by_name=True)
            model_all.load_weights(model_path, by_name=True)

    time_str        = datetime.datetime.strftime(datetime.datetime.now(),'%Y_%m_%d_%H_%M_%S')
    log_dir         = os.path.join(save_dir, "loss_" + str(time_str))
    #--------------------------------------------#
    #   训练参数的设置
    #--------------------------------------------#
    callback        = tf.summary.create_file_writer(log_dir)
    loss_history    = LossHistory(log_dir)

    bbox_util       = BBoxUtility(num_classes)
    roi_helper      = ProposalTargetCreator(num_classes)
    #---------------------------#
    #   读取数据集对应的txt
    #---------------------------#
    with open(train_annotation_path, encoding='utf-8') as f:
        train_lines = f.readlines()
    with open(val_annotation_path, encoding='utf-8') as f:
        val_lines   = f.readlines()
    num_train   = len(train_lines)
    num_val     = len(val_lines)

    show_config(
        classes_path = classes_path, model_path = model_path, input_shape = input_shape, \
        Init_Epoch = Init_Epoch, Freeze_Epoch = Freeze_Epoch, UnFreeze_Epoch = UnFreeze_Epoch, Freeze_batch_size = Freeze_batch_size, Unfreeze_batch_size = Unfreeze_batch_size, Freeze_Train = Freeze_Train, \
        Init_lr = Init_lr, Min_lr = Min_lr, optimizer_type = optimizer_type, momentum = momentum, lr_decay_type = lr_decay_type, \
        save_period = save_period, save_dir = save_dir, num_workers = num_workers, num_train = num_train, num_val = num_val
    )
    wanted_step = 5e4 if optimizer_type == "sgd" else 1.5e4
    total_step  = num_train // Unfreeze_batch_size * UnFreeze_Epoch
    if total_step <= wanted_step:
        wanted_epoch = wanted_step // (num_train // Unfreeze_batch_size) + 1
        print("\n\033[1;33;44m[Warning] 使用%s优化器时，建议将训练总步长设置到%d以上。\033[0m"%(optimizer_type, wanted_step))
        print("\033[1;33;44m[Warning] 本次运行的总训练数据量为%d，Unfreeze_batch_size为%d，共训练%d个Epoch，计算出总训练步长为%d。\033[0m"%(num_train, Unfreeze_batch_size, UnFreeze_Epoch, total_step))
        print("\033[1;33;44m[Warning] 由于总训练步长为%d，小于建议总步长%d，建议设置总世代为%d。\033[0m"%(total_step, wanted_step, wanted_epoch))

    #------------------------------------------------------#
    #   主干特征提取网络特征通用
    #   Init_Epoch为起始世代
    #   Freeze_Epoch为冻结训练的世代
    #   UnFreeze_Epoch总训练世代
    #------------------------------------------------------#
    if True:
        UnFreeze_flag = False
        if Freeze_Train:
            freeze_layers = {'vgg' : 17, 'resnet50' : 141}[backbone]
            for i in range(freeze_layers): 
                if type(model_all.layers[i]) != tf.keras.layers.BatchNormalization:
                    model_all.layers[i].trainable = False
            print('Freeze the first {} layers of total {} layers.'.format(freeze_layers, len(model_all.layers)))

        #-------------------------------------------------------------------#
        #   如果不冻结训练的话，直接设置batch_size为Unfreeze_batch_size
        #-------------------------------------------------------------------#
        batch_size = Freeze_batch_size if Freeze_Train else Unfreeze_batch_size
        
        #-------------------------------------------------------------------#
        #   判断当前batch_size，自适应调整学习率
        #-------------------------------------------------------------------#
        nbs             = 16
        lr_limit_max    = 1e-4 if optimizer_type == 'adam' else 5e-2
        lr_limit_min    = 1e-4 if optimizer_type == 'adam' else 5e-4
        Init_lr_fit     = min(max(batch_size / nbs * Init_lr, lr_limit_min), lr_limit_max)
        Min_lr_fit      = min(max(batch_size / nbs * Min_lr, lr_limit_min * 1e-2), lr_limit_max * 1e-2)

        optimizer = {
            'adam'  : Adam(lr = Init_lr_fit, beta_1 = momentum),
            'sgd'   : SGD(lr = Init_lr_fit, momentum = momentum, nesterov=True)
        }[optimizer_type]
        if ngpus_per_node > 1:
            with strategy.scope():
                model_rpn.compile(
                    loss = {'classification' : rpn_cls_loss(), 'regression' : rpn_smooth_l1()}, optimizer = optimizer
                )
                model_all.compile(
                    loss = {
                        'classification' : rpn_cls_loss(), 'regression' : rpn_smooth_l1(),
                        'dense_class_{}'.format(num_classes) : classifier_cls_loss(), 'dense_regress_{}'.format(num_classes)  : classifier_smooth_l1(num_classes - 1)
                    }, optimizer = optimizer
                )
        else:
            model_rpn.compile(
                loss = {'classification' : rpn_cls_loss(), 'regression' : rpn_smooth_l1()}, optimizer = optimizer
            )
            model_all.compile(
                loss = {
                    'classification' : rpn_cls_loss(), 'regression' : rpn_smooth_l1(),
                    'dense_class_{}'.format(num_classes) : classifier_cls_loss(), 'dense_regress_{}'.format(num_classes)  : classifier_smooth_l1(num_classes - 1)
                }, optimizer = optimizer
            )
    
        #---------------------------------------#
        #   获得学习率下降的公式
        #---------------------------------------#
        lr_scheduler_func = get_lr_scheduler(lr_decay_type, Init_lr_fit, Min_lr_fit, UnFreeze_Epoch)

        epoch_step          = num_train // batch_size
        epoch_step_val      = num_val // batch_size

        if epoch_step == 0 or epoch_step_val == 0:
            raise ValueError('数据集过小，无法进行训练，请扩充数据集。')
        
        train_dataloader    = FRCNNDatasets(train_lines, input_shape, anchors, batch_size, num_classes, train = True)
        val_dataloader      = FRCNNDatasets(val_lines, input_shape, anchors, batch_size, num_classes, train = False)
        
        #---------------------------------------#
        #   构建多线程数据加载器
        #---------------------------------------#
        gen_enqueuer        = OrderedEnqueuer(train_dataloader, use_multiprocessing=True if num_workers > 1 else False, shuffle=True)
        gen_val_enqueuer    = OrderedEnqueuer(val_dataloader, use_multiprocessing=True if num_workers > 1 else False, shuffle=True)
        gen_enqueuer.start(workers=num_workers, max_queue_size=10)
        gen_val_enqueuer.start(workers=num_workers, max_queue_size=10)
        gen                 = gen_enqueuer.get()
        gen_val             = gen_val_enqueuer.get()
        
        for epoch in range(Init_Epoch, UnFreeze_Epoch):
            if epoch >= Freeze_Epoch and not UnFreeze_flag and Freeze_Train:
                batch_size      = Unfreeze_batch_size

                #-------------------------------------------------------------------#
                #   判断当前batch_size，自适应调整学习率
                #-------------------------------------------------------------------#
                nbs             = 16
                lr_limit_max    = 1e-4 if optimizer_type == 'adam' else 5e-2
                lr_limit_min    = 1e-4 if optimizer_type == 'adam' else 5e-4
                Init_lr_fit     = min(max(batch_size / nbs * Init_lr, lr_limit_min), lr_limit_max)
                Min_lr_fit      = min(max(batch_size / nbs * Min_lr, lr_limit_min * 1e-2), lr_limit_max * 1e-2)
                
                #---------------------------------------#
                #   获得学习率下降的公式
                #---------------------------------------#
                lr_scheduler_func = get_lr_scheduler(lr_decay_type, Init_lr_fit, Min_lr_fit, UnFreeze_Epoch)

                for i in range(freeze_layers): 
                    if type(model_all.layers[i]) != tf.keras.layers.BatchNormalization:
                        model_all.layers[i].trainable = True
                                
                if ngpus_per_node > 1:
                    with strategy.scope():
                        model_rpn.compile(
                            loss = {'classification' : rpn_cls_loss(), 'regression' : rpn_smooth_l1()}, optimizer = optimizer
                        )
                        model_all.compile(
                            loss = {
                                'classification' : rpn_cls_loss(), 'regression' : rpn_smooth_l1(),
                                'dense_class_{}'.format(num_classes) : classifier_cls_loss(), 'dense_regress_{}'.format(num_classes)  : classifier_smooth_l1(num_classes - 1)
                            }, optimizer = optimizer
                        )
                else:
                    model_rpn.compile(
                        loss = {'classification' : rpn_cls_loss(), 'regression' : rpn_smooth_l1()}, optimizer = optimizer
                    )
                    model_all.compile(
                        loss = {
                            'classification' : rpn_cls_loss(), 'regression' : rpn_smooth_l1(),
                            'dense_class_{}'.format(num_classes) : classifier_cls_loss(), 'dense_regress_{}'.format(num_classes)  : classifier_smooth_l1(num_classes - 1)
                        }, optimizer = optimizer
                    )
                    
                epoch_step      = num_train // batch_size
                epoch_step_val  = num_val // batch_size

                if epoch_step == 0 or epoch_step_val == 0:
                    raise ValueError("数据集过小，无法继续进行训练，请扩充数据集。")

                train_dataloader.batch_size    = batch_size
                val_dataloader.batch_size      = batch_size
                
                gen_enqueuer.stop()
                gen_val_enqueuer.stop()
                        
                #---------------------------------------#
                #   构建多线程数据加载器
                #---------------------------------------#
                gen_enqueuer        = OrderedEnqueuer(train_dataloader, use_multiprocessing=True if num_workers > 1 else False, shuffle=True)
                gen_val_enqueuer    = OrderedEnqueuer(val_dataloader, use_multiprocessing=True if num_workers > 1 else False, shuffle=True)
                gen_enqueuer.start(workers=num_workers, max_queue_size=10)
                gen_val_enqueuer.start(workers=num_workers, max_queue_size=10)
                gen                 = gen_enqueuer.get()
                gen_val             = gen_val_enqueuer.get()

                UnFreeze_flag = True
                    
            lr = lr_scheduler_func(epoch)
            K.set_value(optimizer.lr, lr)
            
            fit_one_epoch(model_rpn, model_all, loss_history, callback, epoch, epoch_step, epoch_step_val, gen, gen_val, UnFreeze_Epoch,
                    anchors, bbox_util, roi_helper, save_period, save_dir)
