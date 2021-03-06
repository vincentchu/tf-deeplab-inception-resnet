from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys
import os

LIB_DIR = os.path.abspath("./lib")
sys.path.extend([ LIB_DIR ])

import numpy as np
import tensorflow as tf
import deeplab

from pipeline import PipelineManager
from training import average_accuracy, cross_entropy
from labels import to_labels, to_images
from checkpoints import load_checkpoint

BASE_CHECKPOINT = "/mnt/hdd0/datasets/nets/inception_resnet_v2_2016_08_30.ckpt"
OUT_DIR = "/tmp/deeplab"
TARGET_SIZE = [350, 500]

EPOCH_SIZE = 1464
NUM_EPOCHS = 400
BATCH_SIZE = 5
STEPS = int(EPOCH_SIZE * NUM_EPOCHS / BATCH_SIZE)
SAVE_EVERY = 500

def create_and_start_queues(sess):
    manager = PipelineManager("/mnt/hdd0/datasets/pascal/VOCdevkit/VOC2012", "train.txt",
        target_size=TARGET_SIZE, device="/cpu:0", threads=(2*BATCH_SIZE))

    img_queue = manager.create_queues()
    manager.start_queues(sess)

    with tf.device("/cpu:0"):
        image_batch, ground_truth_batch = img_queue.dequeue_up_to(BATCH_SIZE, name="ImageBatchDequeue")

    return manager, image_batch, ground_truth_batch

def create_image_summaries(imgs, gt, predicted):
    with tf.device("/cpu:0"):
        with tf.name_scope("ImageSummaries"):
            im_summ = tf.summary.image("Image", imgs[0:2, :, :, :])
            gt_summ = tf.summary.image("GroundTruth", gt[0:2, :, :, :])

            pred_imgs = to_images(tf.argmax(predicted, axis=3))
            pred_summ = tf.summary.image("Prediction", pred_imgs[0:2, :, :, :])

            return [ im_summ, gt_summ, pred_summ ]

def create_scalar_summaries(*scalars):
    summaries = []
    for scalar in scalars:
        summaries.append(tf.summary.scalar(scalar.name, scalar))

    return summaries

def create_savers(graph):
    summary_writer = tf.summary.FileWriter(OUT_DIR, graph=graph)
    saver = tf.train.Saver(var_list=tf.global_variables(), max_to_keep=10)

    return summary_writer, saver

def save_checkpoint(global_step, sess, saver, summary_writer, avg_accuracy, xentropy, summaries):
    step = tf.train.global_step(sess, global_step)

    if (step % SAVE_EVERY == 0):
        _avg_accuracy, _xentropy, _summaries = sess.run([ avg_accuracy, xentropy, summaries ])

        print("%7d: Accuracy = %7.3f, Xentropy = %7.3f" % (step, 100 * _avg_accuracy, _xentropy))

        model_path = os.path.join(OUT_DIR, "model.ckpt")
        saver.save(sess, model_path, global_step=step)

        for summary in _summaries:
            summary_writer.add_summary(summary, step)

def create_global_step():
    global_step = tf.Variable(0, trainable=False, name="global_step")
    incr_op = tf.assign_add(global_step, 1, name="IncrGlobalStep")

    return global_step, incr_op

def configure_train_step(step, loss_fn):
    base_learning_rate = tf.constant(0.001, dtype=tf.float64, name="base_learning_rate")
    # epoch = tf.cast(tf.floor_div(BATCH_SIZE * step, EPOCH_SIZE, name="current_epoch"), dtype=tf.float64)
    learning_rate = tf.multiply(base_learning_rate, tf.pow(1.0 - (step/STEPS), 0.9),
        name="learning_rate")
    train_step = tf.train.MomentumOptimizer(learning_rate, 0.9).minimize(loss_fn)

    return train_step, learning_rate

def main(_):
    with tf.Session() as sess:
        global_step, incr_op = create_global_step()

        manager, image_batch, ground_truth_batch = create_and_start_queues(sess)
        preds, network_summaries = deeplab.network(image_batch, resize=TARGET_SIZE)
        labeled_ground_truth = to_labels(ground_truth_batch, device="/cpu:0")

        avg_accuracy = average_accuracy(labeled_ground_truth, preds, device="/cpu:0")
        xentropy = cross_entropy(labeled_ground_truth, preds, device="/cpu:0")
        reg_vars = tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES)
        reg_losses = tf.add_n(reg_vars, name="RegularizationLoss")
        total_loss = tf.add(xentropy, reg_losses, name="TotalLoss")

        train_step, learning_rate = configure_train_step(global_step, total_loss)

        img_summaries = create_image_summaries(image_batch, ground_truth_batch, preds)
        scalar_summaries = create_scalar_summaries(xentropy, total_loss, avg_accuracy, learning_rate)
        all_summaries = img_summaries + scalar_summaries + network_summaries

        load_checkpoint(BASE_CHECKPOINT, OUT_DIR, sess)

        sess.run([ tf.local_variables_initializer(), tf.global_variables_initializer() ])
        summary_writer, saver = create_savers(sess.graph)

        for _ in range(STEPS):
            sess.run([ incr_op, train_step ])

            save_checkpoint(global_step, sess, saver, summary_writer, avg_accuracy, xentropy,
                all_summaries)

        manager.stop_queues()
        summary_writer.flush()

if __name__ == '__main__':
    tf.app.run()
