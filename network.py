import torch
import torch.nn as nn
import torch.nn.functional as F

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class EncoderRNN(nn.Module):
    def __init__(self, hidden_size, embedding, num_layers=1, dropout=0, batch_size=1):
        super(EncoderRNN, self).__init__()
        self.num_layers = num_layers
        self.hidden_size = hidden_size
        self.embedding = embedding
        self.num_directions = 1  # Unidirectional
        self.batch_size = batch_size

        # Initialize GRU; the input_size and hidden_size params are both set to 'hidden_size'
        #   because our input size is a word embedding with number of features == hidden_size
        self.gru = nn.GRU(hidden_size, hidden_size, num_layers,
                          dropout=(0 if num_layers == 1 else dropout), bidirectional=True)

        self.hidden = self.initHidden()

    def forward(self, input_seq, input_lengths, hidden=None):
        input_seq = input_seq.to(device)
        # if input_lengths == 1: input_lengths = [len(input_seq)]
        # Convert word indexes to embeddings
        embedded = self.embedding(input_seq)
        # Pack padded batch of sequences for RNN module
        packed = torch.nn.utils.rnn.pack_padded_sequence(embedded, input_lengths)
        # Forward pass through GRU
        outputs, hidden = self.gru(packed, hidden)
        # Unpack padding
        outputs, _ = torch.nn.utils.rnn.pad_packed_sequence(outputs)
        # Sum bidirectional GRU outputs
        outputs = outputs[:, :, :self.hidden_size] + outputs[:, :, self.hidden_size:]
        # Return output and final hidden state
        return outputs, hidden

    def initHidden(self):
        return torch.zeros(self.num_layers * self.num_directions, self.batch_size, self.hidden_size)


# Luong attention layer
class Attn(torch.nn.Module):
    def __init__(self, method, hidden_size):
        super(Attn, self).__init__()
        self.method = method
        if self.method not in ['dot', 'general', 'concat']:
            raise ValueError(self.method, "is not an appropriate attention method.")
        self.hidden_size = hidden_size
        if self.method == 'general':
            self.attn = torch.nn.Linear(self.hidden_size, hidden_size)
        elif self.method == 'concat':
            self.attn = torch.nn.Linear(self.hidden_size * 2, hidden_size)
            self.v = torch.nn.Parameter(torch.FloatTensor(hidden_size))

    def dot_score(self, hidden, encoder_output):
        return torch.sum(hidden * encoder_output, dim=2)

    def general_score(self, hidden, encoder_output):
        energy = self.attn(encoder_output)
        return torch.sum(hidden * energy, dim=2)

    def concat_score(self, hidden, encoder_output):
        energy = self.attn(torch.cat((hidden.expand(encoder_output.size(0), -1, -1), encoder_output), 2)).tanh()
        return torch.sum(self.v * energy, dim=2)

    def forward(self, hidden, encoder_outputs):
        # print("Encoder outputs contains all outputs not just last layer output : ", encoder_outputs.size())
        # Calculate the attention weights (energies) based on the given method
        if self.method == 'general':
            attn_energies = self.general_score(hidden, encoder_outputs)
        elif self.method == 'concat':
            attn_energies = self.concat_score(hidden, encoder_outputs)
        elif self.method == 'dot':
            attn_energies = self.dot_score(hidden, encoder_outputs)

        # Transpose max_length and batch_size dimensions
        attn_energies = attn_energies.t()

        # Return the softmax normalized probability scores (with added dimension)
        return F.softmax(attn_energies, dim=1).unsqueeze(1)


class PlainDecoder(nn.Module):
    def __init__(self, attn_model, embedding, hidden_size, output_size, batch_size=1, num_layers=1, dropout=0.1):
        super(PlainDecoder, self).__init__()

        # Keep for reference
        self.attn_model = attn_model
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.num_layers = num_layers
        self.dropout = dropout
        self.batch_size = batch_size
        self.num_directions = 1  # Assume unidiectional
        # Define layers
        self.embedding = embedding
        self.embedding_dropout = nn.Dropout(dropout)
        self.gru = nn.GRU(hidden_size, hidden_size, num_layers, dropout=(0 if num_layers == 1 else dropout))
        self.concat = nn.Linear(hidden_size * 2, hidden_size)
        self.out = nn.Linear(hidden_size, output_size)

        self.attn = Attn(attn_model, hidden_size)

        self.hidden = self.initHidden()

    def forward(self, input_step, last_hidden, encoder_outputs):
        # Note: we run this one step (word) at a time
        # Get embedding of current input word
        embedded = self.embedding(input_step)
        embedded = self.embedding_dropout(embedded)
        # Forward through unidirectional GRU
        rnn_output, hidden = self.gru(embedded, last_hidden)

        # # Calculate attention weights from the current GRU output
        # attn_weights = self.attn(rnn_output, encoder_outputs)
        # # Multiply attention weights to encoder outputs to get new "weighted sum" context vector
        # context = attn_weights.bmm(encoder_outputs.transpose(0, 1))
        # # Concatenate weighted context vector and GRU output using Luong eq. 5
        # rnn_output = rnn_output.squeeze(0)
        # context = context.squeeze(1)
        # concat_input = torch.cat((rnn_output, context), 1)
        # concat_output = torch.tanh(self.concat(concat_input))
        # # Predict next word using Luong eq. 6
        # output = self.out(concat_output)
        # output = F.softmax(output, dim=1)
        # # Return output and final hidden state
        # return output, hidden

        output = self.out(rnn_output)
        output = output.squeeze(0)
        output = F.softmax(output, dim=1)
        return output, hidden

    def initHidden(self):
        return torch.zeros(self.num_layers * self.num_directions, self.batch_size, self.hidden_size).to(device)


class LuongAttnDecoderRNN(nn.Module):
    def __init__(self, attn_model, embedding, hidden_size, output_size, num_layers=1, dropout=0.1):
        super(LuongAttnDecoderRNN, self).__init__()

        # Keep for reference
        self.attn_model = attn_model
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.num_layers = num_layers
        self.dropout = dropout

        # Define layers
        self.embedding = embedding
        self.embedding_dropout = nn.Dropout(dropout)
        self.gru = nn.GRU(hidden_size, hidden_size, num_layers, dropout=(0 if num_layers == 1 else dropout))
        self.concat = nn.Linear(hidden_size * 2, hidden_size)
        self.out = nn.Linear(hidden_size, output_size)

        self.attn = Attn(attn_model, hidden_size)

    def forward(self, input_step, last_hidden, encoder_outputs):
        # Note: we run this one step (word) at a time
        # Get embedding of current input word
        embedded = self.embedding(input_step)
        embedded = self.embedding_dropout(embedded)
        # Forward through unidirectional GRU
        rnn_output, hidden = self.gru(embedded, last_hidden)
        # Calculate attention weights from the current GRU output
        attn_weights = self.attn(rnn_output, encoder_outputs)
        # Multiply attention weights to encoder outputs to get new "weighted sum" context vector
        context = attn_weights.bmm(encoder_outputs.transpose(0, 1))
        # Concatenate weighted context vector and GRU output using Luong eq. 5
        rnn_output = rnn_output.squeeze(0)
        context = context.squeeze(1)
        concat_input = torch.cat((rnn_output, context), 1)
        concat_output = torch.tanh(self.concat(concat_input))
        # Predict next word using Luong eq. 6
        output = self.out(concat_output)
        output = F.softmax(output, dim=1)
        # Return output and final hidden state
        return output, hidden


def maskNLLLoss(inp, target, mask):
    nTotal = mask.sum()
    crossEntropy = -torch.log(torch.gather(inp, 1, target.view(-1, 1)).squeeze(1))
    loss = crossEntropy.masked_select(mask).mean()
    loss = loss.to(device)
    return loss, nTotal.item()


class RLDecoder(nn.Module):
    def __init__(self, hidden_size, output_size, dropout_p=0.1, max_length=10, num_layers=3):
        super(RLDecoder, self).__init__()

        self.hidden_size = hidden_size
        self.output_size = output_size
        self.dropout_p = dropout_p
        self.max_length = max_length
        self.num_layers = num_layers

        self.embedding = nn.Embedding(self.output_size, self.hidden_size)
        self.attn = nn.Linear(self.hidden_size * 2, self.max_length)
        self.attn_combine = nn.Linear(self.hidden_size * 2, self.hidden_size)
        self.dropout = nn.Dropout(self.dropout_p)
        self.gru = nn.GRU(self.hidden_size, self.hidden_size, num_layers=num_layers)
        self.out = nn.Linear(self.hidden_size, self.output_size)

        hidden0 = torch.zeros(self.num_layers, 1, self.hidden_size)

        hidden0 = hidden0.to(device)
        self.hidden0 = nn.Parameter(hidden0, requires_grad=True)

    def forward(self, input, hidden, encoder_outputs):
        # TODO : The messy attention
        # embedded = self.embedding(input).view(1, 1, -1)
        # embedded = self.dropout(embedded)
        # attn_weights = F.softmax(
        #     self.attn(torch.cat((embedded[0], hidden[0]), 1)), dim=1)
        # attn_applied = torch.bmm(attn_weights.unsqueeze(0),
        #                          encoder_outputs.unsqueeze(0))
        #
        # output = torch.cat((embedded[0], attn_applied[0]), 1)
        # output = self.attn_combine(output).unsqueeze(0)
        #
        # output = F.relu(output)
        # output = F.log_softmax(self.out(output[0]), dim=1)

        embedded = self.embedding(input)
        output, hidden = self.gru(embedded, hidden)
        output = output.squeeze(0)
        output = self.out(output)
        output = F.softmax(output, dim=1)
        return output, hidden

    def initHidden(self):
        return self.hidden0.to(device)
