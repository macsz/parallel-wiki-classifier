import itertools
import math
import multiprocessing
import operator
from sqlalchemy import and_

from collections import Counter
from core.process.classification import Classification
from core.process.clusterization import Clusterization
from core.process.distance import Distance
from core.process.idf import IDF
from core.process.parser import create_parsers
from core.process.reader import Reader
from core.process.svm import SVM
from core.utils import Utils
from data.db import Db, Models
from models.cluster.cluster_center import ClusterCenter
from models.page import Page
from utils.config import get_conf
from utils.log import get_log
from utils.timer import timer

CONF = get_conf()
LOG = get_log()
parsed_docs = {}
largest_id = -1
PROCESSES = int(CONF['general']['processes'])
distances = None
class_distances = None
tokens_idf = {}


class Main(object):
    new_docs = []

    def __init__(self):
        pass

    def _receive_parsed_docs(self, queue_parsed_docs):
        global largest_id
        docs = {}
        processes_returned = 0
        while True:
            doc = queue_parsed_docs.get()
            if not doc:
                processes_returned += 1
                if processes_returned == PROCESSES:
                    break
            else:
                docs[doc.id] = doc
                if largest_id < doc.id:
                    largest_id = doc.id
        LOG.debug('Received {0} parsed docs.'.format(len(docs)))
        return docs

    @timer
    def parse(self):
        global parsed_docs
        global largest_id
        global tokens_idf

        # initialize communication

        queue_unparsed_docs = multiprocessing.Queue()
        queue_parsed_docs = multiprocessing.Queue()
        pipe_tokens_to_idf_parent, pipe_tokens_to_idf_child = multiprocessing.Pipe()
        pipes_tokens_to_processes_parent = []
        pipes_tokens_to_processes_child = []
        for i in range(PROCESSES):
            pipe_tokens_to_processes_parent, pipe_tokens_to_processes_child = \
                multiprocessing.Pipe()
            pipes_tokens_to_processes_parent.append(pipe_tokens_to_processes_parent)
            pipes_tokens_to_processes_child.append(pipe_tokens_to_processes_child)

        # additional pipe to transfer IDF values from IDF process to master
        pipe_idf_master_parent, pipe_idf_master_child = multiprocessing.Pipe()
        pipes_tokens_to_processes_parent.append(pipe_idf_master_parent)

        event = multiprocessing.Event()
        event.clear()

        # set up processes

        ps_reader = Reader(q_unparsed_docs=queue_unparsed_docs)
        ps_parsers = create_parsers(
            queue_unparsed_documents=queue_unparsed_docs,
            pipe_tokens_to_idf_child=pipe_tokens_to_idf_child,
            event=event,
            pipes_tokens_to_processes_child=pipes_tokens_to_processes_child,
            queue_parsed_docs=queue_parsed_docs,
            process_num=PROCESSES
        )
        ps_idf = IDF(
            pipe_tokens_to_idf_parent=pipe_tokens_to_idf_parent,
            docs_num=int(CONF['general']['item_limit']),
            event=event,
            pipes_tokens_to_processes_parent=pipes_tokens_to_processes_parent,
            process_num=PROCESSES
        )

        # read all the articles from XML and do TF-IDF
        ps_reader.start()

        LOG.info("Started processing documents using {0} processes".format(
            PROCESSES))
        for ps_parser in ps_parsers:
            ps_parser.start()
        ps_idf.start()

        # receive tokens IDF values from IDF process
        tokens_idf = pipe_idf_master_child.recv()

        ps_reader.join()
        ps_idf.join()

        # processes will not end until all the data is not received
        parsed_docs = self._receive_parsed_docs(queue_parsed_docs)

        for ps_parser in ps_parsers:
            ps_parser.join()

    @timer
    def distance(self):
        global distances
        LOG.info('Starting calculating distance using {0} processes'.format(
            PROCESSES)
        )
        distances = multiprocessing.Array('d', (largest_id+1)*(largest_id+1))

        dist_ps = []
        for i in range(PROCESSES):
            dist_p = Distance(
                iteration_offset=i,
                iteration_size=PROCESSES,
                distances=distances,
                largest_id=largest_id,
                parsed_docs=parsed_docs
            )
            dist_p.start()
            dist_ps.append(dist_p)

        for dist_p in dist_ps:
            dist_p.join()

        LOG.debug('Distances: \n' + Utils.str_1d_as_2d(distances,
                                                       largest_id+1))
        LOG.info('Done calculating distance for {0} documents'.format(
            len(parsed_docs)))

    @timer
    def cluster(self):
        global distances
        global parsed_docs
        LOG.info('Starting clusterization using {0} processes'.format(
            PROCESSES))

        center_num = int(CONF['clusterization']['centers'])
        centers = Utils.initialize_cluster_centers(
            center_num=center_num,
            start=0,
            end=largest_id,
            parsed_docs=parsed_docs
        )
        new_centers = {}
        LOG.debug('Generated initial centers: {0}'.format(len(centers)))
        LOG.debug('Centers are documents with IDs: {0}'
                  .format(sorted(list(centers.keys()))))

        cluster_ps = []
        pipe_receive_results, pipe_send_results = multiprocessing.Pipe()

        for pid in range(PROCESSES):
            pipe_send_centers, pipe_receive_centers = multiprocessing.Pipe()
            cluster_p = Clusterization(
                offset=pid,
                shift=PROCESSES,
                pipe_send_centers=pipe_send_centers,
                pipe_receive_centers=pipe_receive_centers,
                parsed_docs=parsed_docs,
                distances=distances,
                largest_id=largest_id,
                pipe_send_results=pipe_send_results,
            )
            cluster_p.start()
            cluster_ps.append(cluster_p)

        iteration = 0
        iteration_limit = int(CONF['clusterization']['iterations_limit'])
        changed = False
        docs_num = 0
        while iteration < iteration_limit:
            docs_num = 0
            LOG.debug('Iteration: {0}/{1}'.format(iteration, iteration_limit))
            for cluster_p in cluster_ps:
                cluster_p.pipe_send_centers.send(list(centers.keys()))
            new_centers = {}
            not_finished = PROCESSES
            while not_finished:
                recv = pipe_receive_results.recv()
                if not recv:
                    not_finished -= 1
                else:
                    cid = recv['cid']
                    did = recv['did']
                    dist = recv['dist']
                    centers[cid].add_doc(doc_id=did, distance=dist)
                    parsed_docs[did].center_id = cid
            for cid in centers:
                docs_num += len(centers[cid].doc_ids)
            for cid in centers:
                new_cid = centers[cid].find_closest_doc_to_average()
                if not centers[cid].center_changed:
                    new_cid = cid
                new_center = ClusterCenter()
                new_center.doc_ids = {}
                new_center.pre_doc_ids = {}
                new_center.center_id = new_cid
                new_centers[new_cid] = new_center
                if cid != new_cid:
                    changed = True

            if not changed:
                break
            centers = new_centers
            iteration += 1

        LOG.debug('Finished after {0} iteration(s)'.format(iteration))

        for cluster_p in cluster_ps:
            cluster_p.pipe_send_centers.send(None)
            cluster_p.join()
        print('Docs sum: ', docs_num)
        print('parsed docs: ', len(parsed_docs))
        print('centers:', len(centers))

    @timer
    def _prepare_new_doc(self, doc):
        page = Page()
        page.title = doc.title
        page.content = doc.text
        page.create_tokens()
        # import tokens IDF values from already classified documents
        # TODO check if multiprocessing would be of any benefit
        for page_token in page.tokens:
            try:
                # TODO increment total number of docs by 1
                page_token.idf = tokens_idf[page_token.stem]
            except KeyError:
                # token did not appear in previous documents
                page_token.idf = 1 + math.log((len(parsed_docs) + 1) / 1.0,
                                              math.e)
                LOG.debug('Classification: token \'{0}\' is new.'.format(
                    page_token.stem))
            finally:
                page.calc_tokens_tfidf()
        return page

    @timer
    def classify(self):
        min_doc_id = int(CONF['classification']['new_doc_start_id'])
        max_doc_id = int(CONF['classification']['number']) + min_doc_id
        Db.init()
        session = Db.create_session()
        docs = session.query(Models.Doc).filter(
            and_(max_doc_id > Models.Doc.id, Models.Doc.id >= min_doc_id)
        )
        if docs.count():
            for doc in docs:
                LOG.info('Classifying "{0}"'.format(doc.title))
                new_doc = self._prepare_new_doc(doc)
                class_distances = multiprocessing.Array('d', (largest_id + 1))
                class_ps = []
                for i in range(PROCESSES):
                    class_p = Classification(
                        iteration_offset=i,
                        iteration_size=PROCESSES,
                        class_distances=class_distances,
                        largest_id=largest_id,
                        parsed_docs=parsed_docs,
                        new_doc=new_doc,
                    )
                    class_p.start()
                    class_ps.append(class_p)

                for class_p in class_ps:
                    class_p.join()

                id_dist = []
                for i in range(largest_id + 1):
                    try:
                        item = {
                            'id': i,
                            'distance': class_distances[i],  # distance
                            # between new doc and doc with ID=i
                            'class': parsed_docs[i].center_id  # class of
                            # 'i' doc
                        }
                        id_dist.append(item)
                    except KeyError:
                        pass

                # finding most frequent center in close neighborhood
                id_dist.sort(key=lambda x: x['distance'], reverse=True)
                k_closest = id_dist[:int(CONF['classification']['k'])]
                k_closest = k_closest
                classes = [c['class'] for c in k_closest]
                counted_classes = Counter(classes)
                k_classes = counted_classes

                #########

                new_doc.center_id, _ = counted_classes.most_common(1)[0]
                LOG.info('New doc ({0}) classified as belonging to {1} : {2}'.
                         format(new_doc.title, new_doc.center_id,
                         parsed_docs[new_doc.center_id].title))
                knn_result = parsed_docs[new_doc.center_id]
                LOG.info('KNN group id: {0} ({1})'.format(
                    knn_result.id, knn_result.title))
                LOG.info([parsed_docs[doc].title for doc in parsed_docs if
                          parsed_docs[doc].center_id == new_doc.center_id])
                self.new_docs.append(
                    {
                        'new_doc': new_doc,
                        'knn_result': knn_result,
                        'k_classes': k_classes,
                        'k_closest': k_closest
                    }
                )

        else:
            LOG.info('No documents to classify')

    @timer
    def classify_svm(self):
        for doc in self.new_docs:
            if len(doc['k_classes']) < 2:
                LOG.info('There is only one possible class, not need to run SVM')
                return

            LOG.debug('Document classes for SVM: {0}'.format(doc['k_classes']))

            ### Gather all documents, grouped into classes
            classes_doc = {}
            for class_id in doc['k_classes']:
                # select *ALL* documents that belong to classes indicated by kNN
                docs_id_in_class = [parsed_docs[doc_id].id for doc_id in
                                 parsed_docs if
                                 parsed_docs[doc_id].center_id == class_id]
                classes_doc[class_id] = docs_id_in_class

            pair_queue = multiprocessing.Queue()
            result_queue = multiprocessing.Queue()
            results = {}
            svm_ps = []
            for pid in range(PROCESSES):
                svm_p = SVM(
                    pair_queue=pair_queue,
                    result_queue=result_queue,
                    classes_doc=classes_doc,
                    parsed_docs=parsed_docs,
                    new_doc=doc['new_doc']
                )
                svm_p.start()
                svm_ps.append(svm_p)

            # generate n(n-1)/2 class pairs
            combinations = itertools.combinations(doc['k_classes'], 2)

            for pair in combinations:
                LOG.debug('Sending class pair: {0}'.format(pair))
                pair_queue.put(pair)

            for i in range(PROCESSES):
                pair_queue.put(None)

            not_finished = PROCESSES
            while not_finished:
                res = result_queue.get()
                if not res:
                    not_finished -= 1
                    continue
                LOG.debug('Received SVM pair: {0} {1} with winner: {2}'.format(
                    res['class1'], res['class2'], res['result']))
                try:
                    results[res['result']] += 1
                except:
                    results[res['result']] = 1
            class_id, _ = sorted(results.items(), key=operator.itemgetter(1))[-1]
            LOG.debug('SVM group id: {0} ({1})'.format(
                class_id,
                parsed_docs[class_id].title),
            )
            doc['svm_result'] = parsed_docs[class_id]

            for svm_p in svm_ps:
                svm_p.join()
        print('*' * 20)
        for doc in self.new_docs:
            print('title', doc['new_doc'].title)
            print('knn label', doc['knn_result'].title)
            print('svm label', doc['svm_result'].title)
            print('docs sharing KNN label',
                  [parsed_docs[d].title for d in parsed_docs.keys()
                   if parsed_docs[d].center_id == doc['knn_result'].id])
            print('docs sharing SVM label',
                  [parsed_docs[d].title for d in parsed_docs.keys()
                   if parsed_docs[d].center_id == doc['svm_result'].id])
            print('*' * 20)
        LOG.info('Finished classification')
