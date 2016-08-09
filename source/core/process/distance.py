import multiprocessing
from core.utils import calc_distance
from core.utils import coord_2d_to_1d

class Distance(multiprocessing.Process):
    def __init__(self, iteration_offset, iteration_size, distances,
                 largest_id, parsed_docs):
        """
        This process calculates distance between documents.
        :param iteration_offset: offset by which the iteration will be
        started.
        :param iteration_size: usually should be equal to the number of
        processes working on the same data. Incrementing data cell by
        this value will ensure that each process is working without any
        collisions.
        """
        self.iteration_offset = iteration_offset
        self.iteration_size = iteration_size
        self.distances = distances
        self.largest_id = largest_id
        self.parsed_docs = parsed_docs
        super(self.__class__, self).__init__()

    def run(self):
        row = self.iteration_offset
        while row < (self.largest_id + 1):
            try:
                doc1 = self.parsed_docs[row]
                self.distances[coord_2d_to_1d(row, row, (self.largest_id +
                                                         1))] \
                    = 1.0
                for col in range(row):
                    distance = 0.0
                    try:
                        doc2 = self.parsed_docs[col]
                        distance = calc_distance(doc1, doc2)
                    except:
                        distance = -2
                    self.distances[
                        coord_2d_to_1d(col, row, (self.largest_id + 1))
                    ] = distance
                    self.distances[
                        coord_2d_to_1d(row, col, (self.largest_id + 1))
                    ] = distance
            except:
                # there is no document with such ID, fill it with -1
                # distances
                for col in range(row):
                    self.distances[
                        coord_2d_to_1d(col, row, (self.largest_id + 1))
                    ] = -1
                    self.distances[
                        coord_2d_to_1d(row, col, (self.largest_id + 1))
                    ] = -1
            row += self.iteration_size