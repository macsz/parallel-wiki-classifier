import xml.sax

import multiprocessing
from parsing.wiki_content_handler import WikiContentHandler
from utils.config import get_conf
from utils.exceptions import PageLimitException
from utils.log import get_log
from utils.timer import timer

CONF = get_conf()
LOG = get_log()


class Process(object):
    class Reader(multiprocessing.Process):
        def __init__(self, q_unparsed_documents):
            self._q_unparsed_documents = q_unparsed_documents
            super(self.__class__, self).__init__()

        def run(self):
            wiki_handler = WikiContentHandler(self._q_unparsed_documents)
            sax_parser = xml.sax.make_parser()
            sax_parser.setContentHandler(wiki_handler)

            try:
                data_source = open('../data/wiki_dump.xml')
                sax_parser.parse(data_source)
                LOG.info('Parsed {0} items'.format(wiki_handler.items_saved))
            except PageLimitException as page_limit_exception:
                LOG.info(page_limit_exception)
            except KeyboardInterrupt:
                exit()
            finally:
                # A pill for other threads
                self._q_unparsed_documents.put(None)

    class Parser(multiprocessing.Process):
        def __init__(self, queue_unparsed_documents, pipe_tokens_to_idf_child,
                     event):
            self._queue_unparsed_documents = queue_unparsed_documents
            self._pipe_tokens_to_idf_child = pipe_tokens_to_idf_child
            self._event = event
            super(self.__class__, self).__init__()

        def run(self):
            parsed_docs = 0
            while True:
                page = self._queue_unparsed_documents.get()
                if page is None:
                    # Just to be sure that other threads can also take a pill
                    self._queue_unparsed_documents.put(None)
                    self._pipe_tokens_to_idf_child.send(None)
                    print('Process {0} finished after parsing {1} '
                          'docs'.format(self.pid, parsed_docs))
                    break
                page.create_tokens()
                for token in page.tokens:
                    self._pipe_tokens_to_idf_child.send(token.stem)
                page.content_clean()
                parsed_docs += 1
            print('Process {0} waiting on IDF to finish...'.format(self.pid))
            self._event.wait()
            print('Process {0} finished waiting on IDF...'.format(
                self.pid))

    class IDF(multiprocessing.Process):
        def __init__(self, pipe_tokens_to_idf_parent, docs_num, event):
            self._pipe_tokens_to_idf_parent = pipe_tokens_to_idf_parent
            self._docs_num = docs_num  # total number of documents
            self._event = event
            self._tokens = {}
            super(self.__class__, self).__init__()

        def run(self):
            pills = 0
            while pills < 4:
                msg = self._pipe_tokens_to_idf_parent.recv()
                if msg is None:
                    pills += 1
                    continue
                if msg in self._tokens.keys():
                    self._tokens[msg] += 1
                else:
                    self._tokens[msg] = 1

            self._event.set()

            for token in self._tokens:
                # IDF(token) = 1 + log_e(Total Number Of Documents / Number Of
                # Documents with token in it)
                import math
                token_idf = 1 + math.log(self._docs_num / self._tokens[token],
                                         math.e)
                self._tokens[token] = token_idf

    @staticmethod
    def create_parsers(process_num, queue_unparsed_documents,
                       pipe_tokens_to_idf_child, event):
        processes = []
        for i in range(process_num):
            process = Process.Parser(
                queue_unparsed_documents=queue_unparsed_documents,
                pipe_tokens_to_idf_child=pipe_tokens_to_idf_child,
                event=event
            )
            processes.append(process)
        return processes


@timer
def parse():
    LOG.info("Started loading to database")
    processes = int(CONF['general']['processes'])

    queue_unparsed_documents = multiprocessing.Queue()
    pipe_tokens_to_idf_parent, pipe_tokens_to_idf_child = multiprocessing.Pipe()
    pipes_tokens_to_processes_parent = []
    pipes_tokens_to_processes_childs = []
    event = multiprocessing.Event()
    event.clear()

    ps_reader = Process.Reader(q_unparsed_documents=queue_unparsed_documents)
    ps_parsers = Process.create_parsers(
        process_num=processes,
        queue_unparsed_documents=queue_unparsed_documents,
        pipe_tokens_to_idf_child=pipe_tokens_to_idf_child,
        event=event
    )
    ps_idf = Process.IDF(
        pipe_tokens_to_idf_parent=pipe_tokens_to_idf_parent,
        docs_num=int(CONF['dev']['item_limit']),
        event=event
    )

    ps_reader.start()

    LOG.debug('Spawning {0} parser processes'.format(processes))
    for ps_parser in ps_parsers:
        ps_parser.start()
    ps_idf.start()

    ps_reader.join()
    for ps_parser in ps_parsers:
        ps_parser.join()


if __name__ == '__main__':
    parse()
