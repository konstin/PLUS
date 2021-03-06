# Written by Seonwoo Min, Seoul National University (mswzeus@gmail.com)
# Some parts of the code were referenced from or inspired by below
# - Tristan Bepler's code (https://github.com/tbepler/protein-sequence-embedding-iclr2019)
# PLUS

""" Transmembrane 3line file loading functions """

import numpy as np

import torch


def load_transmembrane(cfg, idx, encoder, sanity_check=False):
    """ load Transmembrane sequence data from 3line file """
    with open(cfg.path[idx], 'rb') as f:
        sequences, labels = [], []
        for sequence, label in parse_3line_stream(f):
            # input sequence length configurations
            sequence = encoder.encode(sequence.upper())
            label = encode_labels(label.upper())
            if cfg.min_len != -1 and len(sequence) < cfg.min_len: continue
            if cfg.max_len != -1 and len(sequence) > cfg.max_len: continue
            if cfg.truncate != -1 and len(sequence) > cfg.truncate: sequence = sequence[:cfg.truncate]
            if sanity_check and len(sequences) == 100: break

            sequences.append(sequence); labels.append(label)

    sequences = [torch.from_numpy(sequence).long() for sequence in sequences]
    labels = [torch.from_numpy(label).long() for label in labels]

    return sequences, labels


def parse_3line_stream(f):
    """ 3line-format file parser """
    for line in f:
        if line.startswith(b'>'):
            x = f.readline().strip()
            y = f.readline().strip()
            yield x, y


def encode_labels(s):
    """ encode region labels for Transmembrane """
    y = np.zeros(len(s), dtype=np.uint8)
    for i in range(len(s)):
        if s[i:i+1] == b'I':    y[i] = 0
        elif s[i:i+1] == b'O':  y[i] = 1
        elif s[i:i+1] == b'M':  y[i] = 2
        else: raise Exception('Unrecognized token' + s[i:i+1].decode('utf-8'))
    return y


## Transmembrane uses a very specific state architecture for HMM
## we can adopt this to describe the transmembrane grammar
class Grammar:
    def __init__(self, n_helix=21):
        ## describe the transmembrane states
        n_states = 3 + 2 * n_helix

        start = np.zeros(n_states)
        start[0] = 1.0  # inner
        start[1] = 1.0  # outer
        start[2] = 1.0  # signal peptide

        end = np.zeros(n_states)
        end[0] = 1.0  # from inner
        end[1] = 1.0  # from outer

        trans = np.zeros((n_states, n_states))
        trans[0, 0] = 1.0  # inner -> inner
        trans[0, 3] = 1.0  # inner -> helix (i->o)
        trans[1, 1] = 1.0  # outer -> outer
        trans[1, 3 + n_helix] = 1.0  # outer -> helix (o->i)

        trans[2, 0] = 1.0  # signal -> inner
        trans[2, 1] = 1.0  # signal -> outer

        for i in range(3, 2 + n_helix):  # i->o helices
            trans[i, i + 1] = 1.0
        trans[2 + n_helix, 1] = 1.0  # helix (i->o) -> outer

        for i in range(3 + n_helix, 2 + 2 * n_helix):  # o->i helices
            trans[i, i + 1] = 1.0
        trans[2 + 2 * n_helix, 0] = 1.0  # helix (o->i) -> inner

        emit = np.zeros((n_states, 3))
        emit[0, 0] = 1.0  # inner
        emit[0, 1] = 1.0
        emit[1, 0] = 1.0  # outer
        emit[1, 1] = 1.0
        for i in range(3, 3 + 2 * n_helix):  # helices
            emit[i, 2] = 1.0

        mapping = np.zeros(n_states, dtype=int)
        mapping[0] = 0
        mapping[1] = 1
        mapping[2] = 3
        mapping[3:3 + 2 * n_helix] = 2

        eps = np.finfo(float).eps
        self.start = np.log(start+eps) - np.log(start.sum()+eps)
        self.end = np.log(end+eps) - np.log(end.sum()+eps)
        self.trans = np.log(trans+eps) - np.log(trans.sum(1, keepdims=True)+eps)
        self.emit = emit
        self.mapping = mapping

    def decode(self, logp):
        p = np.exp(logp)
        z = np.log(np.dot(p, self.emit.T))

        tb = np.zeros(z.shape, dtype=np.int8) - 1
        p0 = z[0] + self.start
        for i in range(z.shape[0] - 1):
            trans = p0[:, np.newaxis] + self.trans + z[i + 1]  #
            tb[i + 1] = np.argmax(trans, 0)
            p0 = np.max(trans, 0)
        # transition to end
        p0 = p0 + self.end
        state = np.argmax(p0)
        score = np.max(p0)
        # traceback most likely sequence of states
        y = np.zeros(z.shape[0], dtype=int)
        j = state
        y[-1] = j
        for i in range(z.shape[0] - 1, 0, -1):
            j = tb[i, j]
            y[i - 1] = j

        # map the states
        y = self.mapping[y]

        return y, score


def transmembrane_regions(y):
    regions = []
    start = -1
    for i in range(len(y)):
        if y[i] == 2 and start < 0:
            start = i
        elif y[i] != 2 and start > 0:
            regions.append((start,i))
            start = -1
    if start > 0:
        regions.append((start, len(y)))
    return regions


def is_prediction_correct(y_hat, y):
    ## prediction is correct if it has the same number of transmembrane regions
    ## and those overlap real transmembrane regions by at least 5 bases
    pred_regions = transmembrane_regions(y_hat)
    target_regions = transmembrane_regions(y)
    if len(pred_regions) != len(target_regions):
        return 0

    for p, t in zip(pred_regions, target_regions):
        if p[1] <= t[0]:
            return 0
        if t[1] <= p[0]:
            return 0
        s = max(p[0], t[0])
        e = min(p[1], t[1])
        overlap = e - s
        if overlap < 5:
            return 0

    return 1
