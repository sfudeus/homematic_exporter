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
import sys

class HomematicMetricsProcessor(threading.Thread):

  METRICS_NAMESPACE = 'homematic'
  DEFAULT_SUPPORTED_TYPES = [
    'HmIP-SWO-PL',
    'HmIP-STH',
    'HmIP-STHD',
    'HMIP-PSM',
    'HmIP-FSM',
    ]

  ccu_host = ''
  ccu_port = ''
  ccu_url = ''
  gathering_interval = 60
  mappedNames = {}
  supported_device_types = DEFAULT_SUPPORTED_TYPES

  device_count = None
  metrics = {}

  def run(self):
    logging.info("Starting thread for data gathering")
    logging.info("Mapping {} devices with custom names".format(len(self.mappedNames)))
    logging.info("Supporting {} device types: {}".format(len(self.supported_device_types), ",".join(self.supported_device_types)))

    gathering_counter = Counter('gathering_count', 'Amount of gathering runs', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)
    error_counter = Counter('gathering_errors', 'Amount of failed gathering runs', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)
    while True:
      gathering_counter.labels(self.ccu_host).inc()
      try:
        self.generateMetrics()
      except OSError as osError:
        logging.info("Failed to generate metrics: {0}".format(osError))
        error_counter.labels(self.ccu_host).inc()
      except:
        logging.info("Failed to generate metrics: {0}".format(sys.exc_info()[0]))
        error_counter.labels(self.ccu_host).inc()
      finally:
        time.sleep(self.gathering_interval)

  def __init__(self, ccu_host, ccu_port, gathering_interval, config_filename):
    super().__init__()

    if config_filename:
      with open(config_filename) as config_file:
        logging.info("Processing config file {}".format(config_filename))
        config = json.load(config_file)
        self.mappedNames = config.get('device_mapping', {})
        self.supported_device_types = config.get('supported_device_types', self.DEFAULT_SUPPORTED_TYPES)

    self.ccu_host = ccu_host
    self.ccu_port = ccu_port
    self.ccu_url = "http://{}:{}".format(args.ccu_host, args.ccu_port)
    self.gathering_interval = int(gathering_interval)
    self.devicecount = Gauge('devicecount', 'Number of processed/supported devices', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)

  def generateMetrics(self):
    logging.info("Gathering metrics")

    with self.createProxy() as proxy:
      for device in self.fetchDevicesList():
        devType = device.get('TYPE')
        devParentType = device.get('PARENT_TYPE')
        devParentAddress = device.get('PARENT')
        devAddress = device.get('ADDRESS')
        if devParentAddress == '' and devType in self.supported_device_types:
          devChildcount = len(device.get('CHILDREN'))
          logging.info("Found top-level device {} of type {} with {} children ".format(devAddress, devType, devChildcount))
          logging.debug(pformat(device))

        if devParentType in self.supported_device_types:
          logging.debug("Found device {} of type {} in supported parent type {}".format(devAddress, devType, devParentType))
          logging.debug(pformat(device))

          if 'VALUES' in device.get('PARAMSETS'):
            paramsetDescription = self.fetchParamSetDescription(devAddress)
            paramset = self.fetchParamSet(devAddress)

            for key in paramsetDescription:
              paramDesc = paramsetDescription.get(key)
              paramType = paramDesc.get('TYPE')
              if paramType in ['FLOAT', 'INTEGER', 'BOOL']:
                self.processSingleValue(devAddress, devType, devParentAddress, devParentType, paramType, key, paramset.get(key), paramDesc.get('TYPE'))
              elif paramType == 'ENUM':
                  logging.debug("Found {}: desc: {} key: {}".format(paramType,paramDesc,paramset.get(key)))
                  self.processSingleValue(devAddress, devType, devParentAddress, devParentType, paramType, key, paramset.get(key), paramDesc.get('VALUE_LIST'))
              else:
                  # ATM Unsupported like HEATING_CONTROL_HMIP.PARTY_TIME_START,
                  # HEATING_CONTROL_HMIP.PARTY_TIME_END, COMBINED_PARAMETER or ACTION
                  logging.debug("Unknown paramType {}, desc: {}, key: {}".format(paramType, paramDesc, paramset.get(key)))

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
        if entry.get('TYPE') in self.supported_device_types or entry.get('PARENT_TYPE') in self.supported_device_types:
          result.append(entry)
      self.devicecount.labels(self.ccu_host).set(len(result))
      return result

  def fetchParamSetDescription(self, address):
    with self.createProxy() as proxy:
      return proxy.getParamsetDescription(address, 'VALUES')

  def fetchParamSet(self, address):
    with self.createProxy() as proxy:
      return proxy.getParamset(address, 'VALUES')

  def processSingleValue(self, deviceAddress, deviceType, parentDeviceAddress, parentDeviceType, paramType, key, value, paramDesc):
    logging.debug("Found {} param {} with value {}".format(paramType, key, value))

    if value != None:
      gaugename = key.lower()
      if not self.metrics.get(gaugename):
        self.metrics[gaugename] = Gauge(gaugename, 'Metrics for ' + key, labelnames=['ccu', 'device', 'device_type', 'parent_device_type', 'mapped_name', 'param_desc'], namespace=self.METRICS_NAMESPACE)
      gauge = self.metrics.get(gaugename)
      if deviceAddress in self.mappedNames:
        mappedName = self.mappedNames[deviceAddress]
      elif parentDeviceAddress in self.mappedNames:
        mappedName = self.mappedNames[parentDeviceAddress]
      else:
        mappedName = deviceAddress
      gauge.labels(ccu=self.ccu_host, device=deviceAddress, device_type=deviceType, parent_device_type=parentDeviceType, mapped_name=mappedName, param_desc=paramDesc).set(value)

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
  parser.add_argument("--config_file", help="A config file with e.g. supported types and device name mappings")
  parser.add_argument("--debug", action="store_true")
  parser.add_argument("--dump_devices", help="Do not start exporter, just dump device list", action="store_true")
  parser.add_argument("--dump_parameters", help="Do not start exporter, just dump device parameters of given device")
  args = parser.parse_args()

  if args.debug:
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
  else:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

  processor = HomematicMetricsProcessor(args.ccu_host, args.ccu_port, args.interval, args.config_file)

  if args.dump_devices:
    print(pformat(processor.fetchDevicesList()))
  elif args.dump_parameters:
    address = args.dump_parameters
    print(pformat(processor.fetchParamSet(address)))
  else:
    processor.start()
    # Start up the server to expose the metrics.
    start_http_server(int(args.port))

