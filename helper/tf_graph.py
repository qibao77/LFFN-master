"""
Paper: "Lightweight Feature Fusion Network for Single Image Super-Resolution"
functions for building tensorflow graph
"""

import logging
import os
import shutil

import tensorflow as tf

from helper import utilty as util


class TensorflowGraph:

	def __init__(self, flags):

		self.name = ""
		# graph settings
		self.cnn_stride = 1
		self.initializer = flags.initializer
		self.weight_dev = flags.weight_dev

		# graph placeholders / objects
		self.is_training = None
		self.dropout = False
		self.saver = None
		self.summary_op = None
		self.train_writer = None
		self.test_writer = None

		# Debugging or Logging
		self.save_loss = flags.save_loss
		self.save_weights = flags.save_weights
		self.save_images = flags.save_images
		self.save_meta_data = flags.save_meta_data

		# Environment (all directory name should not contain '/' after )
		self.checkpoint_dir = flags.checkpoint_dir
		self.tf_log_dir = flags.tf_log_dir

		# status / attributes
		self.Weights = []
		self.Biases = []
		self.features = ""
		self.receptive_fields = 0
		self.complexity = 0
		self.pix_per_input = 1

		self.init_session()

	def init_session(self):
		config = tf.ConfigProto()
		config.gpu_options.allow_growth = False

		print("Session and graph initialized.")
		self.sess = tf.InteractiveSession(config=config, graph=tf.Graph())

	def init_all_variables(self):
		self.sess.run(tf.global_variables_initializer())
		print("Model initialized.")

	def build_activator(self, input_tensor, features: int, activator="", leaky_relu_alpha=0.1, base_name=""):

		features = int(features)
		if activator is None or "":
			return
		elif activator == "relu":
			output = tf.nn.relu(input_tensor, name=base_name + "_relu")
		elif activator == "sigmoid":
			output = tf.nn.sigmoid(input_tensor, name=base_name + "_sigmoid")
		elif activator == "tanh":
			output = tf.nn.tanh(input_tensor, name=base_name + "_tanh")
		elif activator == "leaky_relu":
			output = tf.maximum(input_tensor, leaky_relu_alpha * input_tensor, name=base_name + "_leaky")
		elif activator == "prelu":
			with tf.variable_scope("prelu"):
				alphas = tf.Variable(tf.constant(0.1, shape=[features]), name=base_name + "_prelu")
				if self.save_weights:
					util.add_summaries("prelu_alpha", self.name, alphas, save_stddev=False, save_mean=False)
				output = tf.nn.relu(input_tensor) + tf.multiply(alphas, (input_tensor - tf.abs(input_tensor))) * 0.5
		else:
			raise NameError('Not implemented activator:%s' % activator)

		# self.complexity += (self.pix_per_input * features)

		return output

	def conv2d(self, input_tensor, w, stride, bias=None, use_batch_norm=False, name=""):

		output = tf.nn.conv2d(input_tensor, w, strides=[1, stride, stride, 1], padding="SAME", name=name + "_conv")
		self.complexity += self.pix_per_input * int(w.shape[0] * w.shape[1] * w.shape[2] * w.shape[3])

		if bias is not None:
			output = tf.add(output, bias, name=name + "_add")
			self.complexity += self.pix_per_input * int(bias.shape[0])

		if use_batch_norm:
			output = tf.layers.batch_normalization(output, training=self.is_training, name='BN')

		return output

	def depth_conv2d(self, input_tensor, w, stride, bias=None, use_batch_norm=False, name=""):

		output = tf.nn.depthwise_conv2d(input_tensor, w, strides=[1, stride, stride, 1], padding="SAME",
										name=name + "depth_conv")
		self.complexity += self.pix_per_input * int(w.shape[0] * w.shape[1] * w.shape[2] * w.shape[3])

		if bias is not None:
			output = tf.add(output, bias, name=name + "_add")
			self.complexity += self.pix_per_input * int(bias.shape[0])

		if use_batch_norm:
			output = tf.layers.batch_normalization(output, training=self.is_training, name='BN')

		return output

	#depth wise convolution
	def depth_conv2d_layer(self, name, input_tensor, kernel_size1, kernel_size2, input_feature_num, output_feature_num,
						   use_bias=False, activator=None, initializer="he", use_batch_norm=False, dropout_rate=1.0,
						   reuse=False):
		with tf.variable_scope(name, reuse=reuse):
			w = util.weight([kernel_size1, kernel_size2, input_feature_num, 1],
							stddev=self.weight_dev, name="conv_W", initializer=initializer)

			b = util.bias([output_feature_num], name="conv_B") if use_bias else None
			h = self.depth_conv2d(input_tensor, w, self.cnn_stride, bias=b, use_batch_norm=use_batch_norm, name=name)

			if activator is not None:
				h = self.build_activator(h, output_feature_num, activator, base_name=name)
		return h

	def conv2d_layer(self, name, input_tensor, kernel_size1, kernel_size2, input_feature_num, output_feature_num,
	                 use_bias=False, activator=None, initializer="he", use_batch_norm=False, dropout_rate=1.0, reuse=False):
		with tf.variable_scope(name, reuse=reuse):
			w = util.weight([kernel_size1, kernel_size2, input_feature_num, output_feature_num],
			                stddev=self.weight_dev, name="conv_W", initializer=initializer)

			b = util.bias([output_feature_num], name="conv_B") if use_bias else None
			h = self.conv2d(input_tensor, w, self.cnn_stride, bias=b, use_batch_norm=use_batch_norm, name=name)

			if activator is not None:
				h = self.build_activator(h, output_feature_num, activator, base_name=name)

			if dropout_rate < 1.0:
				h = tf.nn.dropout(h, self.dropout, name="dropout")

		self.Weights.append(w)
		if use_bias:
			self.Biases.append(b)

		return h

	def build_pixel_shuffler_layer(self, name, input_tensor, scale, filters):

		with tf.variable_scope(name):
			up_feature = self.conv2d_layer(name=name+"_CNN", input_tensor=input_tensor, kernel_size1=1,
							  kernel_size2=1, input_feature_num=filters,
							  output_feature_num=scale * scale*filters, activator=None,use_bias=True)
			up_feature = tf.depth_to_space(up_feature, scale)

		return up_feature
	
	def copy_log_to_archive(self, archive_name):

		archive_directory = self.tf_log_dir + '_' + archive_name
		model_archive_directory = archive_directory + '/' + self.name
		util.make_dir(archive_directory)
		util.delete_dir(model_archive_directory)
		try:
			shutil.copytree(self.tf_log_dir, model_archive_directory)
			print("tensorboard log archived to [%s]." % model_archive_directory)
		except OSError as e:
			print(e)
			print("NG: tensorboard log archived to [%s]." % model_archive_directory)

	def load_model(self, name="", trial=0, output_log=False):

		if name == "" or name == "default":
			name = self.name

		if trial > 0:
			filename = self.checkpoint_dir + "/" + name + "_" + str(trial) + ".ckpt"
		else:
			filename = self.checkpoint_dir + "/" + name + ".ckpt"

		if not os.path.isfile(filename + ".index"):
			print("Error. [%s] is not exist!" % filename)
			exit(-1)

		self.saver.restore(self.sess, filename)
		if output_log:
			logging.info("Model restored [ %s ]." % filename)
		else:
			print("Model restored [ %s ]." % filename)

	def save_model(self, name="", trial=0, output_log=False):

		if name == "" or name == "default":
			name = self.name

		if trial > 0:
			filename = self.checkpoint_dir + "/" + name + "_" + str(trial) + ".ckpt"
		else:
			filename = self.checkpoint_dir + "/" + name + ".ckpt"

		self.saver.save(self.sess, filename)
		if output_log:
			logging.info("Model saved [%s]." % filename)
		else:
			print("Model saved [%s]." % filename)

	def build_summary_saver(self):
		if self.save_loss or self.save_weights or self.save_meta_data:
			self.summary_op = tf.summary.merge_all()
			self.train_writer = tf.summary.FileWriter(self.tf_log_dir + "/train")
			self.test_writer = tf.summary.FileWriter(self.tf_log_dir + "/test", graph=self.sess.graph)

		self.saver = tf.train.Saver(max_to_keep=None)
