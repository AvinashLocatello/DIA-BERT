import glob
import math
import os
import pickle
import random
from collections import defaultdict

import numpy as np
import torch
from torch.utils.data import IterableDataset, DataLoader, ConcatDataset


def collate_batch(batch_data):
    """Collate batch of samples."""
    #
    one_batch_rsm = torch.tensor(np.array([batch["rsm"] for batch in batch_data]), dtype=torch.float)
    one_batch_frag_info = torch.tensor(np.array([batch["frag_info"] for batch in batch_data]), dtype=torch.float)
    one_batch_feat = torch.tensor(np.array([batch["feat"] for batch in batch_data]), dtype=torch.float)

    one_batch_rsm = torch.nan_to_num(one_batch_rsm)
    one_batch_frag_info = torch.nan_to_num(one_batch_frag_info)
    one_batch_feat = torch.nan_to_num(one_batch_feat)

    #
    one_batch_file_name = [batch["file"] for batch in batch_data]
    one_batch_precursor_id = [batch["precursor_id"] for batch in batch_data]

    return one_batch_file_name, one_batch_rsm, one_batch_precursor_id, \
        one_batch_feat, one_batch_frag_info


def shuffle_file_list(file_list, seed):
    generator = torch.Generator()
    generator.manual_seed(seed)
    idx = torch.randperm(len(file_list), generator=generator).numpy()
    file_list = (np.array(file_list)[idx]).tolist()
    return file_list


# https://blog.csdn.net/zhang19990111/article/details/131636456
def create_iterable_dataset(data_path,
                            logging,
                            config,
                            parse='train',
                            read_part=True):
    """
    Note: If you want to load all data in the memory, please set "read_part" to False.
    Args:
        :param data_path: A string. dataset's path.
        :param logging: out logging.
        :param config: data from the yaml file.
        :param buffer_size: An integer. the size of file_name buffer.
        :param read_part: BOOL. IterableDiabertDataset if read_part is True, else DataLoader.
    :return:
    """
    valid_file_list = glob.glob(f'{data_path}/*.pkl')

    file_bin_dict = defaultdict(list)
    gpu_num = 1

    #
    for i in range(len(valid_file_list)):
        file_bin_dict[i // 1].append(valid_file_list[i])
    file_bin_list = list(file_bin_dict.keys())

    val_dl = IterableDiabertDataset(file_bin_list,
                                    file_bin_dict=file_bin_dict,
                                    batch_size=config["predict_batch_size"],
                                    gpu_num=gpu_num,
                                    shuffle=False)

    logging.info(
        f"{len(val_dl) * config['predict_batch_size']:,} validation samples"
    )
    return val_dl


class IterableDiabertDataset(IterableDataset):
    """
    Custom dataset class for dataset in order to use efficient
    dataloader tool provided by PyTorch.
    """

    def __init__(self,
                 file_list: list,
                 file_bin_dict=None,
                 batch_size=1024,
                 bath_file_size=1,
                 buffer_size=2,
                 epoch=0,
                 gpu_num=1,
                 shuffle=True,
                 seed=0):
        super(IterableDiabertDataset).__init__()
        #
        self.epoch = epoch
        self.file_list = file_list
        self.file_bin_dict = file_bin_dict
        self.batch_size = batch_size

        self.shuffle = shuffle
        self.seed = seed

        self.gpu_num = gpu_num

        #
        self.bath_file_size = bath_file_size
        self.buffer_size = buffer_size

    def parse_file(self, file_name):
        if self.file_bin_dict is not None:
            data = []
            for bin_file in file_name:
                f = open(bin_file, "rb")
                data.append(pickle.loads(f.read()))
                f.close()
            data = ConcatDataset(data)
        else:
            f = open(file_name, "rb")
            data = pickle.loads(f.read())
            f.close()
        # print('parse_file: ', file_name, flush=True)
        return DataLoader(data,
                          shuffle=False,
                          batch_size=self.batch_size,
                          pin_memory=True,
                          num_workers=0,
                          collate_fn=collate_batch, )

    def set_epoch(self, epoch):
        self.epoch = epoch

    def file_mapper(self, file_list):
        idx = 0
        file_num = len(file_list)
        while idx < file_num:
            if self.file_bin_dict is not None:
                yield self.parse_file(self.file_bin_dict[file_list[idx]])
            else:
                yield self.parse_file(file_list[idx])
            idx += 1

    def __iter__(self):
        if self.gpu_num > 1:
            #
            if 'LOCAL_RANK' in os.environ:
                local_rank = int(os.environ['LOCAL_RANK'])
            else:
                local_rank = 0

            file_itr = self.file_list[local_rank::self.gpu_num]
        else:
            #
            file_itr = self.file_list

        file_mapped_itr = self.file_mapper(file_itr)

        if self.shuffle:
            return self._shuffle(file_mapped_itr)
        else:
            return file_mapped_itr

    def __len__(self):
        if self.gpu_num > 1:
            return math.ceil(len(self.file_list) / self.gpu_num)
        else:
            return len(self.file_list)

    def generate_random_num(self):
        while True:
            random_nums = random.sample(range(self.buffer_size), self.bath_file_size)
            yield from random_nums

    #
    def _shuffle(self, mapped_itr):
        buffer = []
        for dt in mapped_itr:
            #
            if len(buffer) < self.buffer_size:
                buffer.append(dt)
            else:
                i = next(self.generate_random_num())
                yield buffer[i]
                buffer[i] = dt
        random.shuffle(buffer)
        yield from buffer
