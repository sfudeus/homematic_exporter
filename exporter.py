#!/usr/bin/env python3

from prometheus_client import Gauge, Counter, Summary, MetricsHandler, core
from pprint import pformat, pprint
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

import xmlrpc.client
import argparse
import logging
import threading
import time
import json

class HomematicMetricsProcessor(threading.Thread):

  METRICS_NAMESPACE = 'homematic'
  SUPPORTED_TYPES = [ 'HmIP-SWO-PL', 'HmIP-STH' ]

  ccu_host = ''
  ccu_port = ''
  ccu_url = ''
  metrics = {}
  gathering_interval = 60
  device_count = None
  mappedNames = {}

  def run(self):
    logging.info("Starting thread for data gathering")
    gathering_counter = Counter('gathering_count', 'Amount of gathering runs', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)
    while True:
      self.generateMetrics()
      gathering_counter.labels(self.ccu_host).inc()
      time.sleep(60)

  def __init__(self, ccu_host, ccu_port, gathering_interval, mapping_file):
    super().__init__()
    self.ccu_host = ccu_host
    self.ccu_port = ccu_port
    self.ccu_url = "http://{}:{}".format(args.ccu_host, args.ccu_port)
    self.gathering_interval = gathering_interval
    self.devicecount = Gauge('devicecount', 'Number of processed/supported devices', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)

    if mapping_file:
      with open(mapping_file) as json_file:
        self.mappedNames = json.load(json_file)

  def generateMetrics(self):
    logging.info("Gathering metrics")

    with self.createProxy() as proxy:
      for device in self.fetchDevicesList():
        devType = device.get('TYPE')
        devParentType = device.get('PARENT_TYPE')
        devParentAddress = device.get('PARENT')
        devAddress = device.get('ADDRESS')
        if devParentAddress == '' and devType in self.SUPPORTED_TYPES:
          devChildcount = len(device.get('CHILDREN'))
          logging.info("Found top-level device {} of type {} with {} children ".format(devAddress, devType, devChildcount))
          logging.debug(pformat(device))

        if devParentType in self.SUPPORTED_TYPES:
          logging.debug("Found device {} of type {} in supported parent type {}".format(devAddress, devType, devParentType))
          logging.debug(pformat(device))

          if 'VALUES' in device.get('PARAMSETS'):
            paramsetDescription = proxy.getParamsetDescription(devAddress, 'VALUES')
            paramset = proxy.getParamset(devAddress, 'VALUES')

            for key in paramsetDescription:
              paramDesc = paramsetDescription.get(key)
              paramType = paramDesc.get('TYPE')
              if paramType in ['FLOAT', 'INTEGER', 'BOOL']:
                self.processSingleValue(devAddress, devType, devParentAddress, devParentType, paramType, key, paramset.get(key))

            if len(paramset)>0:
              logging.debug("ParamsetDescription for {}".format(devAddress))
              logging.debug(pformat(paramsetDescription))
              logging.debug("Paramset for {}".format(devAddress))
              logging.debug(pformat(paramset))

  def createProxy(self):
    transport = xmlrpc.client.Transport()
    connection = transport.make_connection(self.ccu_host)
    connection.timeout = 5
    return xmlrpc.client.ServerProxy(self.ccu_url, transport=transport)

  def fetchDevicesList(self):
    with self.createProxy() as proxy:
      result = []
      for entry in proxy.listDevices():
        if entry.get('TYPE') in processor.SUPPORTED_TYPES or entry.get('PARENT_TYPE') in processor.SUPPORTED_TYPES:
          result.append(entry)
      self.devicecount.labels(self.ccu_host).set(len(result))
      return result

  def fetchParamSet(self, address):
    with self.createProxy() as proxy:
      return proxy.getParamset(address, 'VALUES')

  def processSingleValue(self, deviceAddress, deviceType, parentDeviceAddress, parentDeviceType, paramType, key, value):
    logging.debug("Found {} param {} with value {}".format(paramType, key, value))

    if value != None:
      gaugename = key.lower()
      if not self.metrics.get(gaugename):
        self.metrics[gaugename] = Gauge(gaugename, 'Metrics for ' + key, labelnames=['ccu', 'device', 'device_type', 'parent_device_type', 'mapped_name'], namespace=self.METRICS_NAMESPACE)
      gauge = self.metrics.get(gaugename)
      if deviceAddress in self.mappedNames:
        mappedName = self.mappedNames[deviceAddress]
      elif parentDeviceAddress in self.mappedNames:
        mappedName = self.mappedNames[parentDeviceAddress]
      else:
        mappedName = deviceAddress
      gauge.labels(ccu=self.ccu_host, device=deviceAddress, device_type=deviceType, parent_device_type=parentDeviceType, mapped_name=mappedName).set(value)

class _ThreadingSimpleServer(ThreadingMixIn, HTTPServer):
  """Thread per request HTTP server."""

def start_http_server(port, addr='', registry=core.REGISTRY):
  """Starts an HTTP server for prometheus metrics as a daemon thread"""
  httpd = _ThreadingSimpleServer((addr, port), MetricsHandler.factory(registry))
  t = threading.Thread(target=httpd.serve_forever)
  t.daemon = False
  t.start()

if __name__ == '__main__':

  parser = argparse.ArgumentParser()
  parser.add_argument("--ccu_host", help="The hostname of the ccu instance", required=True)
  parser.add_argument("--ccu_port", help="The port for the xmlrpc service", default=2010)
  parser.add_argument("--interval", help="The interval between two gathering runs", default=60)
  parser.add_argument("--port", help="The port where to expose the exporter", default=8010)
  parser.add_argument("--mapping_file", help="A file with mapping from addresses to names")
  parser.add_argument("--debug", action="store_true")
  parser.add_argument("--dump_devices", help="Do not start exporter, just dump device list", action="store_true")
  parser.add_argument("--dump_parameters", help="Do not start exporter, just dump device parameters of given device")
  args = parser.parse_args()

  if args.debug:
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
  else:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

  processor = HomematicMetricsProcessor(args.ccu_host, args.ccu_port, args.interval, args.mapping_file)

  if args.dump_devices:
    print(pformat(processor.fetchDevicesList()))
  elif args.dump_parameters:
    address = args.dump_parameters
    print(pformat(processor.fetchParamSet(address)))
  else:
    processor.start()
    # Start up the server to expose the metrics.
    start_http_server(int(args.port))

