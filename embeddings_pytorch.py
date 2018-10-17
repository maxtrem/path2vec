#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Oct  4 13:15:27 2018

@author: dorgham
"""

import torch
import torch.nn as nn
from torch.nn import functional as F
import torch.optim as optim
import helpers
import numpy as np
import random as rn
import argparse
import sys



class Path2VecModel(nn.Module):
    
    def __init__(self, vocab_size, embedding_dim):
        super(Path2VecModel, self).__init__()
        self.embeddings = nn.Embedding(vocab_size, embedding_dim)
        

    def forward(self, inputs):
        embed1 = self.embeddings(inputs[0])
        embed2 = self.embeddings(inputs[1])
        #normalize the vectors before the dot product so that dot product is the cosine proximity between the two vectors
        embed1 = embed1 / embed1.norm(2, 2, True).clamp(min=1e-12).expand_as(embed1)
        embed2 = embed2 / embed2.norm(2, 2, True).clamp(min=1e-12).expand_as(embed2)
        out = torch.sum(embed1*embed2, dim=2)
        
        return out
    
    
def custom_loss(y_pred, y_true, reg_1_output, reg_2_output, use_neighbors):
    if use_neighbors:
        beta = 0.01
        gamma = 0.01
        alpha = 1 - (beta+gamma)
        m_loss = alpha * F.mse_loss(y_pred, y_true, reduction='elementwise_mean')
        
        m_loss -= beta * reg_1_output
        m_loss -= gamma * reg_2_output
    else:
        m_loss = F.mse_loss(y_pred, y_true, reduction='elementwise_mean')

    return m_loss


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Learning graph embeddings with path2vec')
    parser.add_argument('--input_file', required=True,
                        help='tab-separated gzipped file with training pairs and their similarities')
    parser.add_argument('--vsize', type=int, default=300, help='vector size')
    parser.add_argument('--bsize', type=int, default=100, help='batch size')
    parser.add_argument('--lrate', type=float, default=0.001, help='learning rate')
    parser.add_argument('--vocab_file', help='[optional] gzipped JSON file with the vocabulary (list of words)')
    # If the vocabulary file is not provided, it will be inferred from the training set
    # (can be painfully slow for large datasets)
    parser.add_argument('--fix_seeds', type=bool, default=True, help='fix seeds to ensure repeatability')
    parser.add_argument('--use_neighbors', type=bool, default=False,
                        help='whether or not to use the neighbor nodes-based regularizer')
    parser.add_argument('--neighbor_count', type=int, default=3,
                        help='number of adjacent nodes to consider for regularization')
    parser.add_argument('--negative_count', type=int, default=3, help='number of negative samples')
    parser.add_argument('--epochs', type=int, default=10, help='number of training epochs')
    parser.add_argument('--regularize', type=bool, default=False, help='L1 regularization of embeddings')
    args = parser.parse_args()
    
    trainfile = args.input_file  # Gzipped file with pairs and their similarities
    embedding_dimension = args.vsize  # vector size (for example, 20)
    batch_size = args.bsize      # number of pairs in a batch (for example, 10)
    learn_rate = args.lrate   # Learning rate
    neighbors_count = args.neighbor_count
    negative = args.negative_count
    
    print('Using adjacent nodes regularization: ', args.use_neighbors)
    
    if args.fix_seeds:
        #fix seeds for repeatability of experiments
        np.random.seed(42)
        rn.seed(12345)
        torch.manual_seed(1)
        if torch.cuda.is_available():
            torch.cuda.manual_seed(1)
    
    wordpairs = helpers.Wordpairs(trainfile)
    
    if not args.vocab_file:
        print('Building vocabulary from the training set...', file=sys.stderr)
        no_train_pairs, vocab_dict, inverted_vocabulary = helpers.build_vocabulary(wordpairs)
        print('Building vocabulary finished', file=sys.stderr)
    else:
        vocabulary_file = args.vocab_file  # JSON file with the ready-made vocabulary
        print('Loading vocabulary from file', vocabulary_file, file=sys.stderr)
        vocab_dict, inverted_vocabulary = helpers.vocab_from_file(vocabulary_file)
        print('Counting the number of pairs in the training set...')
        no_train_pairs = 0
        for line in wordpairs:
            no_train_pairs += 1
        print('Number of pairs in the training set:', no_train_pairs)
    
    print('Retreiving neighbors of training samples...')
    neighbors_dict = helpers.build_connections(vocab_dict)
    
    vocab_size = len(vocab_dict)
    
    
    model = Path2VecModel(vocab_size, embedding_dimension)
    if torch.cuda.is_available():
        model.cuda()
        torch.cuda.manual_seed(1)
        print("Using GPU...")
    
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    l1_factor = 1e-10
    
    print('Model name and layers:')
    print(model)
    
    #begin the training..
    for epoch in range(args.epochs):
        print('Epoch #', epoch+1)
        total_loss, n_batches = 0, 0
        batchGenerator = helpers.batch_generator_2(wordpairs, vocab_dict, vocab_size, negative, batch_size)
        for batch in batchGenerator:
            n_batches +=1
            l1_reg_term = 0
            inputs, targets = batch
            target_tensor = torch.from_numpy(targets).float()
            
            input_var = torch.Tensor([inputs[0], inputs[1]]).long()
            if torch.cuda.is_available():
                input_var = input_var.cuda()
                target_tensor = target_tensor.cuda()
    
            
            model.zero_grad()
            #do the forward pass
            similarity_pred = model(input_var)

            if args.use_neighbors:
                #get only the positive samples because the batch variable contains the generated negatives as well
                positive_samples = helpers.get_current_positive_samples()
                inputs_list = [[], []]
                for word_idx in positive_samples[0]:
                    neighbors = helpers.get_node_neighbors(word_idx)
                    for neighbor in neighbors:
                        inputs_list[0].append([word_idx])
                        inputs_list[1].append([neighbor])
                        
                input_var = torch.Tensor(inputs_list).long()
                if torch.cuda.is_available():
                    input_var = input_var.cuda()
                    
                reg1_dot_prod = model(input_var)
                reg1_output = torch.sum(reg1_dot_prod) / len(reg1_dot_prod)

                inputs_list = [[], []]
                for word_idx in positive_samples[1]: #context words
                    neighbors = helpers.get_node_neighbors(word_idx)
                    for neighbor in neighbors:
                        inputs_list[0].append([word_idx])
                        inputs_list[1].append([neighbor])
                        
                input_var = torch.Tensor(inputs_list).long()
                if torch.cuda.is_available():
                    input_var = input_var.cuda()
                    
                reg2_dot_prod = model(input_var)
                reg2_output = torch.sum(reg2_dot_prod) / len(reg2_dot_prod)
                   
            # Compute the loss function. 
            loss = custom_loss(similarity_pred, target_tensor, reg1_output, reg2_output, args.use_neighbors)
            if args.regularize == True:
                for param in model.parameters():
                    l1_reg_term += torch.norm(param, 1)
                loss += l1_factor * l1_reg_term
            
            # Do the backward pass and update the gradient
            loss.backward()
            optimizer.step()
    
            # normalize the loss per batch size
            total_loss += loss.item() / len(inputs[0])
            
        print('Total loss = ', total_loss/n_batches)
    
    
    train_name = trainfile.split('.')[0] + '_embeddings_vsize' + str(embedding_dimension) +'_bsize' + str(batch_size) \
                 + '_lr' + str(learn_rate).split('.')[-1]+'_nn-'+str(args.use_neighbors)+str(args.neighbor_count)+\
                 '_reg-'+str(args.regularize)
    filename = train_name + '.vec.gz'
    # Saving the resulting vectors
    embeddings = model.state_dict()['embeddings.weight']
    if torch.cuda.is_available():
        embeddings = embeddings.cpu()
    helpers.save_word2vec_format(filename, vocab_dict, embeddings.numpy())
    
