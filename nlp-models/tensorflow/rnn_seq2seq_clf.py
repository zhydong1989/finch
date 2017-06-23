import tensorflow as tf
import numpy as np
import math


class RNNTextClassifier:
    def __init__(self, seq_len, vocab_size, n_out, embedding_dims=128, cell_size=128,
                 stateful=False, sess=tf.Session()):
        """
        Parameters:
        -----------
        seq_len: int
            Sequence length
        vocab_size: int
            Vocabulary size
        cell_size: int
            Number of units in the rnn cell
        n_out: int
            Output dimensions
        sess: object
            tf.Session() object
        stateful: boolean
            If true, the final state for each batch will be used as the initial state for the next batch 
        """
        self.seq_len = seq_len
        self.vocab_size = vocab_size
        self.embedding_dims = embedding_dims
        self.cell_size = cell_size
        self.n_out = n_out
        self.sess = sess
        self.stateful = stateful
        self._cursor = None
        self.build_graph()
    # end constructor


    def build_graph(self):
        with tf.variable_scope('main_model'):
            self.add_input_layer()
            self.add_word_embedding_layer()
            self.add_lstm_cells()
            self.add_dynamic_rnn()
            self.add_output_layer()
            self.add_backward_path()
        with tf.variable_scope('main_model', reuse=True):
            self.add_inference()
    # end method build_graph


    def add_input_layer(self):
        self.X = tf.placeholder(tf.int64, [None, self.seq_len])
        self.Y = tf.placeholder(tf.int64, [None, self.seq_len])
        self.batch_size = tf.placeholder(tf.int32, [])
        self.rnn_keep_prob = tf.placeholder(tf.float32)
        self.lr = tf.placeholder(tf.float32)
        self.train_flag = tf.placeholder(tf.bool)
        self._cursor = self.X
    # end method add_input_layer


    def add_word_embedding_layer(self):
        E = tf.get_variable('E', [self.vocab_size, self.embedding_dims], tf.float32, tf.random_uniform_initializer(-1, 1))
        self._cursor = tf.nn.embedding_lookup(E, self._cursor)
    # end method add_word_embedding_layer


    def add_lstm_cells(self):
        cell = tf.nn.rnn_cell.LSTMCell(self.cell_size, initializer=tf.orthogonal_initializer)
        cell = tf.nn.rnn_cell.DropoutWrapper(cell, self.rnn_keep_prob)
        self.cell = cell
    # end method add_rnn_cells


    def add_dynamic_rnn(self):
        self.init_state = self.cell.zero_state(self.batch_size, tf.float32)        
        self._cursor, self.final_state = tf.nn.dynamic_rnn(self.cell, self._cursor,
                                                           initial_state=self.init_state, time_major=False)
    # end method add_dynamic_rnn


    def add_output_layer(self):
        self.logits = tf.layers.dense(tf.reshape(self._cursor, [-1, self.cell_size]), self.n_out, name='output')
    # end method add_output_layer


    def add_backward_path(self):
        self.loss = tf.contrib.seq2seq.sequence_loss(
            logits = tf.reshape(self.logits, [self.batch_size, self.seq_len, self.n_out]),
            targets = self.Y,
            weights = tf.ones([self.batch_size, self.seq_len]),
            average_across_timesteps = True,
            average_across_batch = True,
        )
        self.acc = tf.reduce_mean(tf.cast(tf.equal(tf.argmax(self.logits, 1),
                                                   tf.reshape(self.Y, [-1])), tf.float32))
        self.train_op = tf.train.AdamOptimizer(self.lr).minimize(self.loss)
    # end method add_backward_path


    def add_inference(self):
        self.X_ = tf.placeholder(tf.int32, [None, 1])
        self.init_state_ = self.cell.zero_state(1, tf.float32)
        X = tf.nn.embedding_lookup(tf.get_variable('E'), self.X_)
        Y, self.final_state_ = tf.nn.dynamic_rnn(self.cell, X, initial_state=self.init_state_, time_major=False)
        Y = tf.layers.dense(tf.reshape(Y, [-1, self.cell_size]), self.n_out, name='output', reuse=True)
        self.softmax_out_ = tf.nn.softmax(Y)
    # end add_sample_model


    def fit(self, X, Y, val_data=None, n_epoch=10, batch_size=128, en_exp_decay=True, en_shuffle=True,
            rnn_keep_prob=1.0):
        if val_data is None:
            print("Train %d samples" % len(X) )
        else:
            print("Train %d samples | Test %d samples" % (len(X), len(val_data[0])))
        log = {'loss':[], 'acc':[], 'val_loss':[], 'val_acc':[]}
        global_step = 0

        self.sess.run(tf.global_variables_initializer()) # initialize all variables
        for epoch in range(n_epoch): # batch training
            if en_shuffle:
                shuffled = np.random.permutation(len(X))
                X = X[shuffled]
                Y = Y[shuffled]
            local_step = 1
            next_state = self.sess.run(self.init_state, feed_dict={self.batch_size:batch_size})

            for X_batch, Y_batch in zip(self.gen_batch(X, batch_size),
                                        self.gen_batch(Y, batch_size)):
                lr = self.decrease_lr(en_exp_decay, global_step, n_epoch, len(X), batch_size)
                if (self.stateful) and (len(X_batch) == batch_size):
                    _, next_state, loss, acc = self.sess.run([self.train_op, self.final_state, self.loss, self.acc],
                                                             {self.X:X_batch, self.Y:Y_batch,
                                                              self.batch_size:batch_size,
                                                              self.rnn_keep_prob:rnn_keep_prob,
                                                              self.lr:lr, self.init_state:next_state})
                else:             
                    _, loss, acc = self.sess.run([self.train_op, self.loss, self.acc],
                                                 {self.X:X_batch, self.Y:Y_batch,
                                                  self.batch_size:len(X_batch), self.lr:lr,
                                                  self.rnn_keep_prob:rnn_keep_prob})
                local_step += 1
                global_step += 1
                if local_step % 50 == 0:
                    print ('Epoch %d/%d | Step %d/%d | train_loss: %.4f | train_acc: %.4f | lr: %.4f'
                           %(epoch+1, n_epoch, local_step, int(len(X)/batch_size), loss, acc, lr))

            if val_data is not None: # go through testing data, average validation loss and ac 
                val_loss_list, val_acc_list = [], []
                next_state = self.sess.run(self.init_state, feed_dict={self.batch_size:batch_size})
                for X_test_batch, Y_test_batch in zip(self.gen_batch(val_data[0], batch_size),
                                                      self.gen_batch(val_data[1], batch_size)):
                    if (self.stateful) and (len(X_test_batch) == batch_size):
                        v_loss, v_acc, next_state = self.sess.run([self.loss, self.acc, self.final_state],
                                                                  {self.X:X_test_batch, self.Y:Y_test_batch,
                                                                   self.batch_size:batch_size,
                                                                   self.rnn_keep_prob:1.0,
                                                                   self.init_state:next_state})
                    else:
                        v_loss, v_acc = self.sess.run([self.loss, self.acc],
                                                      {self.X:X_test_batch, self.Y:Y_test_batch,
                                                       self.batch_size:len(X_test_batch),
                                                       self.rnn_keep_prob:1.0})
                    val_loss_list.append(v_loss)
                    val_acc_list.append(v_acc)
                val_loss, val_acc = self.list_avg(val_loss_list), self.list_avg(val_acc_list)

            # append to log
            log['loss'].append(loss)
            log['acc'].append(acc)
            if val_data is not None:
                log['val_loss'].append(val_loss)
                log['val_acc'].append(val_acc)

            # verbose
            if val_data is None:
                print ("Epoch %d/%d | train_loss: %.4f | train_acc: %.4f |" % (epoch+1, n_epoch, loss, acc),
                       "lr: %.4f" % (lr) )
            else:
                print ("Epoch %d/%d | train_loss: %.4f | train_acc: %.4f |" % (epoch+1, n_epoch, loss, acc),
                       "test_loss: %.4f | test_acc: %.4f |" % (val_loss, val_acc), "lr: %.4f" % (lr) )
        # end "for epoch in range(n_epoch)"

        return log
    # end method fit


    def predict(self, X_test, batch_size=128):
        batch_pred_list = []
        next_state = self.sess.run(self.init_state, feed_dict={self.batch_size:batch_size})
        for X_test_batch in self.gen_batch(X_test, batch_size):
            if (self.stateful) and (len(X_test_batch) == batch_size):
                batch_pred, next_state = self.sess.run([self.logits, self.final_state], 
                                                       {self.X:X_test_batch, self.batch_size:batch_size,
                                                        self.rnn_keep_prob:1.0,
                                                        self.init_state:next_state})
            else:
                batch_pred = self.sess.run(self.logits,
                                          {self.X:X_test_batch, self.batch_size:len(X_test_batch),
                                           self.rnn_keep_prob:1.0})
            batch_pred_list.append(batch_pred)
        return np.argmax(np.vstack(batch_pred_list), 1)
    # end method predict


    def infer(self, xs):
        next_state = self.sess.run(self.init_state_)
        ys = []
        for x in xs:
            x = np.atleast_2d(x)
            y, next_state = self.sess.run([self.softmax_out_, self.final_state_], 
                                          {self.X_: x,
                                           self.rnn_keep_prob: 1.0,
                                           self.init_state_: next_state})
            ys.append(y)
        return np.vstack(ys)
    # end method infer


    def gen_batch(self, arr, batch_size):
        for i in range(0, len(arr), batch_size):
            yield arr[i : i+batch_size]
    # end method gen_batch


    def decrease_lr(self, en_exp_decay, global_step, n_epoch, len_X, batch_size):
        if en_exp_decay:
            max_lr = 0.005
            min_lr = 0.0005
            decay_rate = math.log(min_lr/max_lr) / (-n_epoch*len_X/batch_size)
            lr = max_lr*math.exp(-decay_rate*global_step)
        else:
            lr = 0.001
        return lr
    # end method adjust_lr


    def list_avg(self, l):
        return sum(l) / len(l)
    # end method list_avg
# end class