#!/usr/bin/env python

import chainer
from chainer import cuda
import chainer.serializers as S
import fcn

import cv_bridge
from jsk_topic_tools import ConnectionBasedTransport
from jsk_topic_tools.log_utils import jsk_loginfo
import message_filters
import numpy as np
import rospy
from sensor_msgs.msg import Image


is_torch_available = True
try:
    import torch
    import torchfcn
except ImportError:
    is_torch_available = False


def assert_torch_available():
    if not is_torch_available:
        raise RuntimeError(
            'PyTorch is unavailable. Please install it by:'
            ' rosrun jsk_perception install_pytorch.sh')


class FCNObjectSegmentation(ConnectionBasedTransport):

    def __init__(self):
        super(self.__class__, self).__init__()
        self.backend = rospy.get_param('~backend', 'chainer')
        self.gpu = rospy.get_param('~gpu', -1)  # -1 is cpu mode
        self.target_names = rospy.get_param('~target_names')
        self.bg_label = rospy.get_param('~bg_label', 0)
        self.proba_threshold = rospy.get_param('~proba_threshold', 0.0)
        self.mean_bgr = np.array([104.00698793, 116.66876762, 122.67891434])
        self._load_model()
        self.pub = self.advertise('~output', Image, queue_size=1)
        self.pub_proba = self.advertise(
            '~output/proba_image', Image, queue_size=1)

    def _load_model(self):
        if self.backend == 'chainer':
            self._load_chainer_model()
        elif self.backend == 'torch':
            assert_torch_available()
            self._load_torch_model()
        else:
            raise RuntimeError('Unsupported backend: %s', self.backend)

    def _load_model_chainer_backend(self):
        model_name = rospy.get_param('~model_name')
        model_h5 = rospy.get_param('~model_h5')
        n_class = len(self.target_names)
        if model_name == 'fcn32s':
            self.model = fcn.models.FCN32s(n_class=n_class)
        elif model_name == 'fcn16s':
            self.model = fcn.models.FCN16s(n_class=n_class)
        elif model_name == 'fcn8s':
            self.model = fcn.models.FCN8s(n_class=n_class)
        else:
            raise ValueError('Unsupported ~model_name: {}'.format(model_name))
        jsk_loginfo('Loading trained model: {0}'.format(model_h5))
        S.load_hdf5(model_h5, self.model)
        jsk_loginfo('Finished loading trained model: {0}'.format(model_h5))
        if self.gpu != -1:
            self.model.to_gpu(self.gpu)
        self.model.train = False

    def _load_torch_model(self):
        n_class = len(self.target_names)
        model_file = rospy.get_param('~model_file')
        model_name = rospy.get_param('~model_name')
        if model_name == 'fcn32s':
            self.model = torchfcn.models.FCN32s(n_class=n_class)
        elif model_name == 'fcn32s_bilinear':
            self.model = torchfcn.models.FCN32s(n_class=n_class, deconv=False)
        else:
            raise ValueError('Unsupported ~model_name: {0}'.format(model_name))
        jsk_loginfo('Loading trained model: %s' % model_file)
        self.model.load_state_dict(torch.load(model_file))
        jsk_loginfo('Finished loading trained model: %s' % model_file)
        if self.gpu >= 0:
            self.model = self.model.cuda(self.gpu)
        self.model.eval()

    def subscribe(self):
        use_mask = rospy.get_param('~use_mask', False)
        if use_mask:
            queue_size = rospy.get_param('~queue_size', 10)
            sub_img = message_filters.Subscriber(
                '~input', Image, queue_size=1, buff_size=2**24)
            sub_mask = message_filters.Subscriber(
                '~input/mask', Image, queue_size=1, buff_size=2**24)
            self.subs = [sub_img, sub_mask]
            if rospy.get_param('~approximate_sync', False):
                slop = rospy.get_param('~slop', 0.1)
                sync = message_filters.ApproximateTimeSynchronizer(
                    fs=self.subs, queue_size=queue_size, slop=slop)
            else:
                sync = message_filters.TimeSynchronizer(
                    fs=self.subs, queue_size=queue_size)
            sync.registerCallback(self._cb_with_mask)
        else:
            # larger buff_size is necessary for taking time callback
            # http://stackoverflow.com/questions/26415699/ros-subscriber-not-up-to-date/29160379#29160379  # NOQA
            sub_img = rospy.Subscriber(
                '~input', Image, self._cb, queue_size=1, buff_size=2**24)
            self.subs = [sub_img]

    def unsubscribe(self):
        for sub in self.subs:
            sub.unregister()

    def _cb_with_mask(self, img_msg, mask_msg):
        br = cv_bridge.CvBridge()
        img = br.imgmsg_to_cv2(img_msg, desired_encoding='bgr8')
        mask = br.imgmsg_to_cv2(mask_msg, desired_encoding='mono8')
        label, proba_img = self.segment(img)
        if label is None or proba_img is None:
            return
        label[mask == 0] = 0
        proba_img[:, :, 0][mask != 0] = 1
        proba_img[:, :, 1:][mask != 0] = 0
        label_msg = br.cv2_to_imgmsg(label.astype(np.int32), '32SC1')
        label_msg.header = img_msg.header
        self.pub.publish(label_msg)
        proba_msg = br.cv2_to_imgmsg(proba_img.astype(np.float32))
        proba_msg.header = img_msg.header
        self.pub_proba.publish(proba_msg)

    def _cb(self, img_msg):
        br = cv_bridge.CvBridge()
        img = br.imgmsg_to_cv2(img_msg, desired_encoding='bgr8')
        label, proba_img = self.segment(img)
        if label is None or proba_img is None:
            return
        label_msg = br.cv2_to_imgmsg(label.astype(np.int32), '32SC1')
        label_msg.header = img_msg.header
        self.pub.publish(label_msg)
        proba_msg = br.cv2_to_imgmsg(proba_img.astype(np.float32))
        proba_msg.header = img_msg.header
        self.pub_proba.publish(proba_msg)

    def segment(self, bgr):
        if self.backend == 'chainer':
            return self._segment_chainer_backend(bgr)
        elif self.backend == 'torch':
            assert_torch_available()
            return self._segment_torch_backend(bgr)
        raise ValueError('Unsupported backend: {0}'.format(self.backend))

    def _segment_chainer_backend(self, bgr):
        blob = (bgr - self.mean_bgr).transpose((2, 0, 1))
        x_data = np.array([blob], dtype=np.float32)
        if self.gpu != -1:
            x_data = cuda.to_gpu(x_data, device=self.gpu)
        x = chainer.Variable(x_data, volatile=True)
        self.model(x)
        proba_img = chainer.functions.softmax(self.model.score)
        proba_img = chainer.functions.transpose(proba_img, (1, 2, 0))
        max_proba_img = chainer.functions.max(proba_img, axis=-1)
        label = chainer.functions.argmax(self.model.score, axis=1)
        # gpu -> cpu
        proba_img = cuda.to_cpu(proba_img.data)[0]
        max_proba_img = cuda.to_cpu(max_proba_img.data)[0]
        label = cuda.to_cpu(label.data)[0]
        # uncertain because the probability is low
        label[max_proba_img < self.proba_threshold] = self.bg_label
        return label, proba_img

    def _segment_torch_backend(self, bgr):
        blob = (bgr - self.mean_bgr).transpose((2, 0, 1))
        x_data = np.array([blob], dtype=np.float32)
        x_data = torch.from_numpy(x_data)
        if self.gpu >= 0:
            x_data = x_data.cuda(self.gpu)
        x = torch.autograd.Variable(x_data, volatile=True)
        score = self.model(x)
        proba = torch.nn.functional.softmax(score)
        max_proba, label = torch.max(proba, 1)
        # uncertain because the probability is low
        label[max_proba < self.proba_threshold] = self.bg_label
        # gpu -> cpu
        score = score.permute(0, 2, 3, 1).data.cpu().numpy()[0]
        proba = proba.permute(0, 2, 3, 1).data.cpu().numpy()[0]
        max_proba = max_proba.data.cpu().numpy().squeeze((0, 1))
        label = label.data.cpu().numpy().squeeze((0, 1))
        # uncertain because the probability is low
        label[max_proba < self.proba_threshold] = self.bg_label
        return label, proba


if __name__ == '__main__':
    rospy.init_node('fcn_object_segmentation')
    FCNObjectSegmentation()
    rospy.spin()
