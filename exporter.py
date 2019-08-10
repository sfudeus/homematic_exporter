#!/usr/local/bin/python3

from prometheus_client import Gauge, Counter, Summary, MetricsHandler, core
from pprint import pformat
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

import xmlrpc.client
import argparse
import logging
import threading
import time

class HomematicMetricsProcessor(threading.Thread):

  METRICS_NAMESPACE = 'homematic'
  SUPPORTED_TYPES = [ 'HmIP-SWO-PL', 'HmIP-STH']

  ccu_host = ''
  ccu_port = ''
  ccu_url = ''
  metrics = {}
  gathering_interval = 60

  def run(self):
    logging.info("Starting thread for data gathering")
    gathering_summary = Summary('gathering_count', 'Amount of gathering runs', labelnames=['instance'], namespace=self.METRICS_NAMESPACE, labelvalues=[self.ccu_host])
    while True:
      with gathering_summary.time():
        self.generateMetrics()
      time.sleep(60)

  def __init__(self, ccu_host, ccu_port, gathering_interval):
    super().__init__()
    self.ccu_host = ccu_host
    self.ccu_port = ccu_port
    self.ccu_url = "http://{}:{}".format(args.ccu_host, args.ccu_port)
    self.gathering_interval = gathering_interval


  def generateMetrics(self):
    logging.info("Gathering metrics")

    with xmlrpc.client.ServerProxy(self.ccu_url) as proxy:
      devices = proxy.listDevices()
      for device in devices:
        devType = device.get('TYPE')
        devParentType = device.get('PARENT_TYPE')
        devAddress = device.get('ADDRESS')
        if device.get('PARENT') == '' and devType in self.SUPPORTED_TYPES:
          devChildcount = len(device.get('CHILDREN'))
          logging.info("Found top-level device {} of type {} with {} children ".format(devAddress, devType, devChildcount))
          logging.debug(pformat(device))

        if devParentType in self.SUPPORTED_TYPES:
          logging.debug("Found device {} of type {} in supported parent type {}".format(devAddress, devType, devParentType))
          logging.debug(pformat(device))

          if 'VALUES' in device.get('PARAMSETS') and devType != 'MAINTENANCE':
            paramsetDescription = proxy.getParamsetDescription(devAddress, 'VALUES')
            paramset = proxy.getParamset(devAddress, 'VALUES')

            for key in paramsetDescription:
              paramDesc = paramsetDescription.get(key)
              paramType = paramDesc.get('TYPE')
              if paramType == 'FLOAT' or paramType == "INTEGER":
                self.processSingleValue(devAddress, devType, devParentType, paramType, key, paramset.get(key))

            if len(paramset)>0:
              logging.debug("ParamsetDescription for {}".format(devAddress))
              logging.debug(pformat(paramsetDescription))
              logging.debug("Paramset for {}".format(devAddress))
              logging.debug(pformat(paramset))

  def processSingleValue(self, deviceAddress, deviceType, parentDeviceType, paramType, key, value):
    logging.debug("Found {} param {} with value {}".format(paramType, key, value))

    if value != None:
      gaugename = key.lower()
      if not self.metrics.get(gaugename):
        self.metrics[gaugename] = Gauge(gaugename, 'Metrics for ' + key, labelnames=['instance', 'device', 'device_type', 'parent_device_type'], namespace=self.METRICS_NAMESPACE)
      gauge = self.metrics.get(gaugename)
      gauge.labels(instance=self.ccu_host, device=deviceAddress, device_type=deviceType, parent_device_type=parentDeviceType).set(value)

class _ThreadingSimpleServer(ThreadingMixIn, HTTPServer):
  """Thread per request HTTP server."""

def start_http_server(port, addr='', registry=core.REGISTRY):
  """Starts an HTTP server for prometheus metrics as a daemon thread"""
  httpd = _ThreadingSimpleServer((addr, port), MetricsHandler.factory(registry))
  t = threading.Thread(target=httpd.serve_forever)
  t.daemon = False
  t.start()

def start_gathering(ccu_host, ccu_port, interval):
  t = HomematicMetricsProcessor(ccu_host, ccu_port, interval)
  t.start()

if __name__ == '__main__':

  parser = argparse.ArgumentParser()
  parser.add_argument("--ccu_host", help="The hostname of the ccu instance", required=True)
  parser.add_argument("--ccu_port", help="The port for the xmlrpc service", default=2010)
  parser.add_argument("--interval", help="The interval between two gathering runs", default=60)
  parser.add_argument("--port", help="The port where to expose the exporter", default=8010)
  parser.add_argument("--debug", action="store_true")
  args = parser.parse_args()

  if args.debug:
    logging.basicConfig(level=logging.DEBUG)
  else:
    logging.basicConfig(level=logging.INFO)

  start_gathering(args.ccu_host, args.ccu_port, args.interval)

  # Start up the server to expose the metrics.
  start_http_server(int(args.port))
