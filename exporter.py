#!/usr/bin/env python3

import xmlrpc.client
import argparse
import logging
import threading
import time
import json
import sys

from socketserver import ThreadingMixIn
from http.server import HTTPServer
from pprint import pformat
from prometheus_client import Gauge, Counter, Enum, MetricsHandler, core


class HomematicMetricsProcessor(threading.Thread):

  METRICS_NAMESPACE = 'homematic'
  DEFAULT_SUPPORTED_TYPES = [
    'HmIP-SWO-PL',
    'HmIP-STH',
    'HmIP-STHD',
    'HMIP-PSM',
    'HmIP-FSM',
    'HmIP-SWD',
    'HMIP-SWDO',
    ]

  ccu_host = ''
  ccu_port = ''
  ccu_url = ''
  gathering_interval = 60
  mapped_names = {}
  supported_device_types = DEFAULT_SUPPORTED_TYPES

  device_count = None
  metrics = {}

  def run(self):
    logging.info("Starting thread for data gathering")
    logging.info("Mapping {} devices with custom names".format(len(self.mapped_names)))
    logging.info("Supporting {} device types: {}".format(len(self.supported_device_types), ",".join(self.supported_device_types)))

    gathering_counter = Counter('gathering_count', 'Amount of gathering runs', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)
    error_counter = Counter('gathering_errors', 'Amount of failed gathering runs', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)
    while True:
      gathering_counter.labels(self.ccu_host).inc()
      try:
        self.generate_metrics()
      except OSError as os_error:
        logging.info("Failed to generate metrics: {0}".format(os_error))
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
        self.mapped_names = config.get('device_mapping', {})
        self.supported_device_types = config.get('supported_device_types', self.DEFAULT_SUPPORTED_TYPES)

    self.ccu_host = ccu_host
    self.ccu_port = ccu_port
    self.ccu_url = "http://{}:{}".format(ccu_host, ccu_port)
    self.gathering_interval = int(gathering_interval)
    self.devicecount = Gauge('devicecount', 'Number of processed/supported devices', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)

  def generate_metrics(self):
    logging.info("Gathering metrics")

    for device in self.fetch_devices_list():
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
          paramsetDescription = self.fetch_param_set_description(devAddress)
          paramset = self.fetch_param_set(devAddress)

          for key in paramsetDescription:
            paramDesc = paramsetDescription.get(key)
            paramType = paramDesc.get('TYPE')
            if paramType in ['FLOAT', 'INTEGER', 'BOOL']:
              self.process_single_value(devAddress, devType, devParentAddress, devParentType, paramType, key, paramset.get(key))
            elif paramType == 'ENUM':
              logging.debug("Found {}: desc: {} key: {}".format(paramType, paramDesc, paramset.get(key)))
              self.process_enum(devAddress, devType, devParentAddress, devParentType, paramType, key, paramset.get(key), paramDesc.get('VALUE_LIST'))
            else:
              # ATM Unsupported like HEATING_CONTROL_HMIP.PARTY_TIME_START,
              # HEATING_CONTROL_HMIP.PARTY_TIME_END, COMBINED_PARAMETER or ACTION
              logging.debug("Unknown paramType {}, desc: {}, key: {}".format(paramType, paramDesc, paramset.get(key)))

          if paramset:
            logging.debug("ParamsetDescription for {}".format(devAddress))
            logging.debug(pformat(paramsetDescription))
            logging.debug("Paramset for {}".format(devAddress))
            logging.debug(pformat(paramset))

  def create_proxy(self):
    transport = xmlrpc.client.Transport()
    connection = transport.make_connection(self.ccu_host)
    connection.timeout = 5
    return xmlrpc.client.ServerProxy(self.ccu_url, transport=transport)

  def fetch_devices_list(self):
    with self.create_proxy() as proxy:
      result = []
      for entry in proxy.listDevices():
        if entry.get('TYPE') in self.supported_device_types or entry.get('PARENT_TYPE') in self.supported_device_types:
          result.append(entry)
      self.devicecount.labels(self.ccu_host).set(len(result))
      return result

  def fetch_param_set_description(self, address):
    with self.create_proxy() as proxy:
      return proxy.getParamsetDescription(address, 'VALUES')

  def fetch_param_set(self, address):
    with self.create_proxy() as proxy:
      return proxy.getParamset(address, 'VALUES')

  def resolve_mapped_name(self, deviceAddress, parentDeviceAddress):
    if deviceAddress in self.mapped_names:
      return self.mapped_names[deviceAddress]
    elif parentDeviceAddress in self.mapped_names:
      return self.mapped_names[parentDeviceAddress]
    else:
      return deviceAddress

  def process_single_value(self, deviceAddress, deviceType, parentDeviceAddress, parentDeviceType, paramType, key, value):
    logging.debug("Found {} param {} with value {}".format(paramType, key, value))

    if value is not None:
      gaugename = key.lower()
      if not self.metrics.get(gaugename):
        self.metrics[gaugename] = Gauge(gaugename, 'Metrics for ' + key, labelnames=['ccu', 'device', 'device_type', 'parent_device_type', 'mapped_name'], namespace=self.METRICS_NAMESPACE)
      gauge = self.metrics.get(gaugename)
      gauge.labels(
        ccu=self.ccu_host,
        device=deviceAddress,
        device_type=deviceType,
        parent_device_type=parentDeviceType,
        mapped_name=self.resolve_mapped_name(deviceAddress, parentDeviceAddress)).set(value)

  def process_enum(self, deviceAddress, deviceType, parentDeviceAddress, parentDeviceType, paramType, key, value, istates):
    if value is None:
      return

    gaugename = key.lower()+"_set"
    logging.debug("Found {} param {} with value {}, gauge {}".format(paramType, key, value, gaugename))

    if not self.metrics.get(gaugename):
      self.metrics[gaugename] = Enum(gaugename, 'Metrics for ' + key, states=istates, labelnames=['ccu', 'device', 'device_type', 'parent_device_type', 'mapped_name'], namespace=self.METRICS_NAMESPACE)
    gauge = self.metrics.get(gaugename)
    mapped_name_v = self.resolve_mapped_name(deviceAddress, parentDeviceAddress)
    state = istates[int(value)]
    logging.debug("Setting {} to value {} item {}".format(mapped_name_v, str(value), state))
    gauge.labels(
      ccu=self.ccu_host,
      device=deviceAddress,
      device_type=deviceType,
      parent_device_type=parentDeviceType,
      mapped_name=mapped_name_v
      ).state(state)

class _ThreadingSimpleServer(ThreadingMixIn, HTTPServer):
  """Thread per request HTTP server."""

def start_http_server(port, addr='', registry=core.REGISTRY):
  """Starts an HTTP server for prometheus metrics as a daemon thread"""
  httpd = _ThreadingSimpleServer((addr, port), MetricsHandler.factory(registry))
  thread = threading.Thread(target=httpd.serve_forever)
  thread.daemon = False
  thread.start()

if __name__ == '__main__':

  PARSER = argparse.ArgumentParser()
  PARSER.add_argument("--ccu_host", help="The hostname of the ccu instance", required=True)
  PARSER.add_argument("--ccu_port", help="The port for the xmlrpc service", default=2010)
  PARSER.add_argument("--interval", help="The interval between two gathering runs", default=60)
  PARSER.add_argument("--port", help="The port where to expose the exporter", default=8010)
  PARSER.add_argument("--config_file", help="A config file with e.g. supported types and device name mappings")
  PARSER.add_argument("--debug", action="store_true")
  PARSER.add_argument("--dump_devices", help="Do not start exporter, just dump device list", action="store_true")
  PARSER.add_argument("--dump_parameters", help="Do not start exporter, just dump device parameters of given device")
  ARGS = PARSER.parse_args()

  if ARGS.debug:
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
  else:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

  PROCESSOR = HomematicMetricsProcessor(ARGS.ccu_host, ARGS.ccu_port, ARGS.interval, ARGS.config_file)

  if ARGS.dump_devices:
    print(pformat(PROCESSOR.fetch_devices_list()))
  elif ARGS.dump_parameters:
    print(pformat(PROCESSOR.fetch_param_set(ARGS.dump_parameters)))
  else:
    PROCESSOR.start()
    # Start up the server to expose the metrics.
    start_http_server(int(ARGS.port))
