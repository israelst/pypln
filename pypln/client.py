#!/usr/bin/env python
# coding: utf-8

from logging import Logger, NullHandler
from copy import deepcopy
import zmq


class ManagerClient(object):
    def __init__(self, logger=None, logger_name='ManagerClient'):
        self.context = zmq.Context()
        if logger is None:
            self.logger = Logger(logger_name)
            self.logger.addHandler(NullHandler())
        else:
            self.logger = logger

    def connect(self, api_host_port, broadcast_host_port):
        self.api_host_port = api_host_port
        self.broadcast_host_port = broadcast_host_port
        self.api_connection_string = 'tcp://{}:{}'.format(*api_host_port)
        self.broadcast_connection_string = \
                'tcp://{}:{}'.format(*broadcast_host_port)

        self.manager_api = self.context.socket(zmq.REQ)
        self.manager_broadcast = self.context.socket(zmq.SUB)

        self.manager_api.connect(self.api_connection_string)
        self.manager_broadcast.connect(self.broadcast_connection_string)

    def __del__(self):
        self.close_sockets()

    def close_sockets(self):
        sockets = ['manager_api', 'manager_broadcast']
        for socket in sockets:
            if hasattr(self, socket):
                getattr(self, socket).close()

class Worker(object):
    def __init__(self, worker_name):
        self.name = worker_name
        self.after = []

    def then(self, *after):
        self.after = after
        return self

class Pipeline(object):
    def __init__(self, pipeline, api_host_port, broadcast_host_port,
                 logger=None, logger_name='Pipeline', time_to_wait=0.1):
        self.client = ManagerClient(logger, logger_name)
        self.client.connect(api_host_port, broadcast_host_port)
        self.pipeline = pipeline
        self.time_to_wait = time_to_wait
        self.logger = self.client.logger

    def send_job(self, worker):
        job = {'command': 'add job', 'worker': worker.name,
               'document': worker.document}
        self.client.manager_api.send_json(job)
        self.logger.info('Sent job: {}'.format(job))
        message = self.client.manager_api.recv_json()
        self.logger.info('Received from Manager API: {}'.format(message))
        self.waiting[message['job id']] = worker
        subscribe_message = 'job finished: {}'.format(message['job id'])
        self.client.manager_broadcast.setsockopt(zmq.SUBSCRIBE,
                                                 subscribe_message)
        self.logger.info('Subscribed on Manager Broadcast to: {}'\
                         .format(subscribe_message))

    def distribute(self):
        self.waiting = {}
        for document in self.documents:
            worker = deepcopy(self.pipeline)
            worker.document = document
            self.send_job(worker)

    def run(self, documents):
        self.documents = documents
        self.distribute()
        try:
            while True:
                if self.client.manager_broadcast.poll(self.time_to_wait):
                    message = self.client.manager_broadcast.recv()
                    self.logger.info('[Client] Received from broadcast: {}'\
                                     .format(message))
                    if message.startswith('job finished: '):
                        #TODO: unsubscribe
                        job_id = message.split(': ')[1].split(' ')[0]
                        worker = self.waiting[job_id]
                        for next_worker in worker.after:
                            next_worker.document = worker.document
                            self.send_job(next_worker)
                        del self.waiting[job_id]
                if not self.waiting.keys():
                    break
        except KeyboardInterrupt:
            self.client.close_sockets()

def main():
    import os
    from logging import Logger, StreamHandler, Formatter
    from sys import stdout, argv
    from pymongo import Connection
    from gridfs import GridFS


    if len(argv) == 1:
        print 'ERROR: you need to pass files to import'
        exit(1)

    api_host_port = ('localhost', 5555)
    broadcast_host_port = ('localhost', 5556)
    #TODO: should get config from manager
    config = {'db': {'host': 'localhost', 'port': 27017,
                     'database': 'pypln',
                     'collection': 'documents',
                     'gridfs collection': 'files',
                     'monitoring collection': 'monitoring'},
              'monitoring interval': 60,}
    db_config = config['db']
    mongo_connection = Connection(db_config['host'], db_config['port'])
    db = mongo_connection[db_config['database']]
    if 'username' in db_config and 'password' in db_config and \
            db_config['username'] and db_config['password']:
           db.authenticate(db_config['username'], db_config['password'])
    gridfs = GridFS(db, db_config['gridfs collection'])
    #TODO: connection/collection lines should be in pypln.stores.mongodb

    logger = Logger('Pipeline')
    handler = StreamHandler(stdout)
    formatter = Formatter('%(asctime)s - %(name)s - %(levelname)s - '
                          '%(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    my_docs = []
    filenames = argv[1:]
    logger.info('Inserting files...')
    for filename in filenames:
        if os.path.exists(filename):
            logger.debug('  {}'.format(filename))
            doc_id = gridfs.put(open(filename).read(), filename=filename)
            my_docs.append(str(doc_id))

    #TODO: use et2 to create the tree/pipeline image
    W, W.__call__ = Worker, Worker.then
    workers = W('extractor')(W('tokenizer')(W('pos'),
                                            W('freqdist')))
    pipeline = Pipeline(workers, api_host_port, broadcast_host_port, logger)
    pipeline.run(my_docs)
    #TODO: should receive a 'job error' from manager when some job could not be
    #      processed (timeout, worker not found etc.)


if __name__ == '__main__':
    main()
