#!/usr/bin/env python3

import xmlrpc.client
import argparse
import logging
import threading
import time
import json
import re
import sys

from socketserver import ThreadingMixIn
from http.server import HTTPServer
from pprint import pformat
import requests
from prometheus_client import Gauge, Counter, Enum, MetricsHandler, core, Summary, start_http_server

class HomematicMetricsProcessor(threading.Thread):

    METRICS_NAMESPACE = 'homematic'
    # Supported Homematic (BidcosRF and IP) device types
    DEFAULT_SUPPORTED_TYPES = [
        'HmIP-eTRV-2',
        'HmIP-eTRV-C',
        'HmIP-eTRV-C-2',
        'HmIP-FSM',
        'HmIP-MIOB',
        'HMIP-PS',
        'HMIP-PSM',
        'HmIP-RCV-1',
        'HmIP-STH',
        'HmIP-STHD',
        'HmIP-STHO',
        'HmIP-STE2-PCB',
        'HmIP-SWD',
        'HMIP-SWDO',
        'HmIP-SWSD',
        'HmIP-SWO-PL',
        'HmIP-SWO-PR',
        'HmIP-WTH-2',
        'HmIP-BSL',
        'HM-CC-RT-DN',
        'HM-Dis-EP-WM55',
        'HM-Dis-WM55',
        'HM-ES-PMSw1-Pl-DN-R5',
        'HM-ES-TX-WM',
        'HM-LC-Bl1-FM',
        'HM-LC-Dim1PWM-CV',
        'HM-LC-Dim1T-FM',
        'HM-LC-RGBW-WM',
        'HM-LC-Sw1-Pl-DN-R5',
        'HM-LC-Sw1-FM',
        'HM-LC-Sw2-FM',
        'HM-OU-CFM-Pl',
        'HM-OU-CFM-TW',
        'HM-PBI-4-FM',
        'HM-PB-2-WM55',
        'HM-PB-6-WM55',
        'HM-RC-P1',
        'HM-RC-4-2',
        'HM-RC-8',
        'HM-Sec-MDIR-2',
        'HM-Sec-SCo',
        'HM-Sec-SC-2',
        'HM-Sec-SD-2',
        'HM-Sec-TiS',
        'HM-Sen-LI-O',
        'HM-Sen-MDIR-O',
        'HM-Sen-MDIR-WM55',
        'HM-SwI-3-FM',
        'HM-TC-IT-WM-W-EU',
        'HM-WDS10-TH-O',
        'HM-WDS100-C6-O-2',
        'HM-WDS30-OT2-SM',
        'HM-WDS40-TH-I',
        'HM-WDS40-TH-I-2',
    ]

    # A list with channel numbers for devices where getParamset
    # never works, or only sometimes works (e.g. if the device sent
    # no data since the last CCU reboot).
    DEFAULT_CHANNELS_WITH_ERRORS_ALLOWED = {
        'HM-ES-PMSw1-Pl-DN-R5': [1, 2],
        'HM-ES-TX-WM': [1],
        'HM-LC-Bl1-FM': [1],
        'HM-LC-Dim1PWM-CV': [1, 2, 3],
        'HM-LC-Dim1T-FM': [1],
        'HM-LC-RGBW-WM': [1, 2, 3],
        'HM-LC-Sw1-Pl-DN-R5': [1],
        'HM-LC-Sw1-FM': [1, 2],
        'HM-LC-Sw2-FM': [1, 2],
        'HM-OU-CFM-Pl': [1, 2],
        'HM-OU-CFM-TW': [1, 2],
        'HM-Sen-MDIR-O': [1],
        'HM-Sen-MDIR-WM55': [3],
        'HM-TC-IT-WM-W-EU': [7],
        'HM-WDS30-OT2-SM': [1, 2, 3, 4, 5],
    }

    ccu_host = ''
    ccu_port = ''
    ccu_url = ''
    auth = None
    gathering_interval = 60
    reload_device_mappings_active = False
    reload_device_mappings_interval = 30  # reload devices every 30 gatherings
    device_mappings = {}
    supported_device_types = DEFAULT_SUPPORTED_TYPES
    channels_with_errors_allowed = DEFAULT_CHANNELS_WITH_ERRORS_ALLOWED

    device_count = None
    metrics = {}
    metrics_to_keep = {}

    def run(self):
        logging.info("Starting thread for data gathering")
        logging.info("Mapping {} devices with custom mapping".format(len(self.device_mappings)))
        logging.info("Supporting {} device types: {}".format(len(self.supported_device_types), ",".join(self.supported_device_types)))

        gathering_counter = Counter('gathering_count', 'Amount of gathering runs', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)
        error_counter = Counter('gathering_errors', 'Amount of failed gathering runs', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)
        generate_metrics_summary = Summary('generate_metrics_seconds', 'Time spent in gathering runs',
                                           labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)
        remove_metrics_summary = Summary('remove_metrics_seconds', 'Time spent removing old metrics from the collector in python', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)
        read_device_mappings_summary = Summary('read_device_mappings_seconds', 'Time spent reading device mappings from CCU', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)

        gathering_loop_counter = 1

        if len(self.device_mappings) == 0:
            # if no custom device mappings are given we use them from the ccu.
            self.reload_device_mappings_active = True

            with read_device_mappings_summary.labels(self.ccu_host).time():
                self.device_mappings = self.read_device_mapping()
            logging.info("Read {} device mappings from CCU".format(len(self.device_mappings)))

        while True:
            if self.reload_device_mappings_active:
                if gathering_loop_counter % self.reload_device_mappings_interval == 0:
                    try:
                        with read_device_mappings_summary.labels(self.ccu_host).time():
                            self.device_mappings = self.read_device_mapping()
                        logging.info("Read {} device mappings from CCU".format(len(self.device_mappings)))
                    except OSError as os_error:
                        logging.info("Failed to read device mappings: {0}".format(os_error))
                        error_counter.labels(self.ccu_host).inc()
                    except BaseException:
                        logging.info("Failed to read device mappings: {0}".format(sys.exc_info()))
                        error_counter.labels(self.ccu_host).inc()

                    try:
                        # remove old device/channel metrics that were not refreshed in the previous run,
                        # leaving global metrics, and default python metrics untouched
                        # removal of old metrics is necessary for devices/channels that are no longer existent
                        removed_metrics_count = 0
                        with remove_metrics_summary.labels(self.ccu_host).time():
                            for collector_name, collector in self.metrics.items():
                                for metric in collector._metrics.copy().keys():
                                    if metric not in self.metrics_to_keep.get(collector_name, ()):
                                        collector.remove(*metric)
                                        removed_metrics_count += 1
                        logging.info("Removed {} old device metrics from collector in python".format(removed_metrics_count))
                    except BaseException:
                        logging.info("Failed to remove old device metrics: {0}".format(sys.exc_info()))
                        error_counter.labels(self.ccu_host).inc()

            gathering_counter.labels(self.ccu_host).inc()
            try:
                with generate_metrics_summary.labels(self.ccu_host).time():
                    self.generate_metrics()
                    self.refresh_time = time.time()
            except OSError as os_error:
                logging.exception("Failed to generate metrics: {0}".format(os_error))
                error_counter.labels(self.ccu_host).inc()
            except BaseException:
                logging.exception("Failed to generate metrics: {0}".format(sys.exc_info()))
                error_counter.labels(self.ccu_host).inc()
            finally:
                time.sleep(self.gathering_interval)
            gathering_loop_counter += 1

    def __init__(self, ccu_host, ccu_port, auth, gathering_interval, reload_device_mappings_interval, config_filename):
        super().__init__()

        if config_filename:
            with open(config_filename) as config_file:
                logging.info("Processing config file {}".format(config_filename))
                config = json.load(config_file)
                self.device_mappings = config.get('device_mapping', {})
                self.supported_device_types = config.get('supported_device_types', self.DEFAULT_SUPPORTED_TYPES)
                self.channels_with_errors_allowed = config.get('channels_with_errors_allowed', self.DEFAULT_CHANNELS_WITH_ERRORS_ALLOWED)

        self.ccu_host = ccu_host
        self.ccu_port = ccu_port
        if auth:
            self.auth = auth
            self.ccu_url = "http://{}:{}@{}:{}".format(auth[0], auth[1], ccu_host, ccu_port)
        else:
            self.ccu_url = "http://{}:{}".format(ccu_host, ccu_port)
        self.gathering_interval = int(gathering_interval)
        self.reload_device_mappings_interval = int(reload_device_mappings_interval)
        self.devicecount = Gauge('devicecount', 'Number of processed/supported devices', labelnames=['ccu'], namespace=self.METRICS_NAMESPACE)
        # Upon request export the seconds since the last successful update.
        # This is robust against internal crashes and can be used by the healthcheck.
        self.refresh_time = time.time()
        self.refresh_age = Gauge("refresh_age_seconds", "Seconds since the last successful refresh.", labelnames=["ccu"], namespace=self.METRICS_NAMESPACE)
        self.refresh_age.labels(self.ccu_host).set_function(lambda: time.time() - self.refresh_time)

    def generate_metrics(self):
        logging.info("Gathering metrics")

        metrics_of_this_iteration = {}

        for device in self.fetch_devices_list():
            devType = device.get('TYPE')
            devParentType = device.get('PARENT_TYPE')
            devParentAddress = device.get('PARENT')
            devAddress = device.get('ADDRESS')
            if devParentAddress == '':
                if devType in self.supported_device_types:
                    devChildcount = len(device.get('CHILDREN'))
                    logging.info("Found top-level device {} of type {} with {} child devices (channels)".format(devAddress, devType, devChildcount))
                    logging.debug(pformat(device))
                else:
                    logging.info("Found unsupported top-level device {} of type {}".format(devAddress, devType))
            # the following if block will never be executed for top-level devices (actual devices) only child devices (channels)
            # therefore, in this if block device corresponds to a channel and parent device is the actual device
            if devParentType in self.supported_device_types:
                logging.debug("Found child device (channel) {} of type {} in supported parent device type {}".format(devAddress, devType, devParentType))
                logging.debug(pformat(device))

                allowFailedChannel = False
                invalidChannels = self.channels_with_errors_allowed.get(devParentType)
                if invalidChannels is not None:
                    channel = int(devAddress[devAddress.find(":") + 1:])
                    if channel in invalidChannels:
                        allowFailedChannel = True

                if 'VALUES' in device.get('PARAMSETS'):
                    paramsetDescription = self.fetch_param_set_description(devAddress)
                    try:
                        paramset = self.fetch_param_set(devAddress)
                    except xmlrpc.client.Fault:
                        if allowFailedChannel:
                            logging.debug("Error reading paramset for child device (channel) {} of type {} in parent deivce type {} (expected)".format(
                                devAddress, devType, devParentType))
                        else:
                            logging.debug("Error reading paramset for child device (channel) {} of type {} in parent device type {} (unexpected)".format(
                                devAddress, devType, devParentType))
                            raise

                    for key in paramsetDescription:
                        paramDesc = paramsetDescription.get(key)
                        paramType = paramDesc.get('TYPE')
                        if paramType in ['FLOAT', 'INTEGER', 'BOOL']:
                            metric_name, metric = self.process_single_value(
                                devAddress, devType,
                                devParentAddress, devParentType,
                                paramType, key, paramset.get(key)
                            )
                            if metric is not None and metric_name not in metrics_of_this_iteration:
                                metrics_of_this_iteration[metric_name] = [metric]
                            elif metric is not None:
                                metrics_of_this_iteration[metric_name] += [metric]
                        elif paramType == 'ENUM':
                            logging.debug("Found {}: desc: {} key: {}".format(paramType, paramDesc, paramset.get(key)))
                            metric_name, metric = self.process_enum(
                                devAddress, devType,
                                devParentAddress, devParentType,
                                key, paramset.get(key), paramDesc.get('VALUE_LIST')
                            )
                            if metric is not None and metric_name not in metrics_of_this_iteration:
                                metrics_of_this_iteration[metric_name] = [metric]
                            elif metric is not None:
                                metrics_of_this_iteration[metric_name] += [metric]
                        else:
                            # ATM Unsupported like HEATING_CONTROL_HMIP.PARTY_TIME_START,
                            # HEATING_CONTROL_HMIP.PARTY_TIME_END, COMBINED_PARAMETER or ACTION
                            logging.debug("Unknown paramType {}, desc: {}, key: {}".format(paramType, paramDesc, paramset.get(key)))

                    if paramset:
                        logging.debug("ParamsetDescription for {}".format(devAddress))
                        logging.debug(pformat(paramsetDescription))
                        logging.debug("Paramset for {}".format(devAddress))
                        logging.debug(pformat(paramset))

        self.metrics_to_keep = metrics_of_this_iteration

    def create_proxy(self):
        transport = xmlrpc.client.Transport()
        connection = transport.make_connection(self.ccu_host)
        connection.timeout = 5
        return xmlrpc.client.ServerProxy(self.ccu_url, transport=transport)

    def fetch_devices_list(self):
        with self.create_proxy() as proxy:
            result = []
            for entry in proxy.listDevices():
                result.append(entry)
            self.devicecount.labels(self.ccu_host).set(len(result))
            return result

    def fetch_param_set_description(self, address):
        with self.create_proxy() as proxy:
            return proxy.getParamsetDescription(address, 'VALUES')

    def fetch_param_set(self, address):
        with self.create_proxy() as proxy:
            return proxy.getParamset(address, 'VALUES')

    def is_default_device_address(self, deviceAddress):
        return re.match("^[0-9a-f]{14}:[0-9]+$", deviceAddress, re.IGNORECASE)

    def resolve_device_mapping(self, deviceAddress, parentDeviceAddress):
        # TODO: investigate if first if block is required
        # not sure why this first if block is required, because this function is only called within if blocks that
        # restrict to child devices and this deviceAddress corresponds to a channel and never to an actual device
        if deviceAddress in self.device_mappings and not self.is_default_device_address(deviceAddress):
            return self.device_mappings.get(deviceAddress)
        # to my understanding only this elif block can ever get a match from the device mapping because every child
        # device must belong to a parent device and the parent device it the actual device in the device mapping
        elif parentDeviceAddress in self.device_mappings:
            return {
                k: v for k, v in self.device_mappings.get(parentDeviceAddress).items() if k not in ("channels")
            } | {
                "channel": self.device_mappings.get(parentDeviceAddress).get("channels", {}).get(deviceAddress, {})
            }
        else:
            return {"name": deviceAddress}

    def process_single_value(self, deviceAddress, deviceType, parentDeviceAddress, parentDeviceType, paramType, key, value):
        logging.debug("Found {} param {} with value {}".format(paramType, key, value))

        # this function is only executed in if blocks that filter for child devices (channels)
        # therefore, we can always assume that device corresponds to a channel and parent device to the actual device

        gaugename = key.lower()

        if value == '' or value is None:
            return gaugename, None

        if not self.metrics.get(gaugename):
            self.metrics[gaugename] = Gauge(gaugename, 'Metrics for ' + key, labelnames=[
                'ccu',
                'channel_address',
                'channel_type',
                'channel_id',
                'channel_name',
                'channel_room_id',
                'channel_room_name',
                'channel_function_id',
                'channel_function_name',
                'device_address',
                'device_type',
                'device_id',
                'device_name',
                'device_room_id',
                'device_room_name',
                'device_function_id',
                'device_function_name'
            ], namespace=self.METRICS_NAMESPACE)
        gauge = self.metrics.get(gaugename)
        deviceMapping = self.resolve_device_mapping(deviceAddress, parentDeviceAddress)
        gauge.labels(
            ccu=self.ccu_host,
            channel_address=deviceAddress,
            channel_type=deviceType,
            channel_id=deviceMapping.get("channel", {}).get("id"),
            channel_name=deviceMapping.get("channel", {}).get("name"),
            channel_room_id=deviceMapping.get("channel", {}).get("firstRoom", {}).get("id"),
            channel_room_name=deviceMapping.get("channel", {}).get("firstRoom", {}).get("name"),
            channel_function_id=deviceMapping.get("channel", {}).get("firstFunction", {}).get("id"),
            channel_function_name=deviceMapping.get("channel", {}).get("firstFunction", {}).get("name"),
            device_address=parentDeviceAddress,
            device_type=parentDeviceType,
            device_id=deviceMapping.get("id"),
            device_name=deviceMapping.get("name"),
            device_room_id=deviceMapping.get("mainRoom", {}).get("id"),
            device_room_name=deviceMapping.get("mainRoom", {}).get("name"),
            device_function_id=deviceMapping.get("mainFunction", {}).get("id"),
            device_function_name=deviceMapping.get("mainFunction", {}).get("name")
        ).set(value)

        return gaugename, (
            str(self.ccu_host),
            str(deviceAddress),
            str(deviceType),
            str(deviceMapping.get("channel", {}).get("id")),
            str(deviceMapping.get("channel", {}).get("name")),
            str(deviceMapping.get("channel", {}).get("firstRoom", {}).get("id")),
            str(deviceMapping.get("channel", {}).get("firstRoom", {}).get("name")),
            str(deviceMapping.get("channel", {}).get("firstFunction", {}).get("id")),
            str(deviceMapping.get("channel", {}).get("firstFunction", {}).get("name")),
            str(parentDeviceAddress),
            str(parentDeviceType),
            str(deviceMapping.get("id")),
            str(deviceMapping.get("name")),
            str(deviceMapping.get("mainRoom", {}).get("id")),
            str(deviceMapping.get("mainRoom", {}).get("name")),
            str(deviceMapping.get("mainFunction", {}).get("id")),
            str(deviceMapping.get("mainFunction", {}).get("name"))
        )

    def process_enum(self, deviceAddress, deviceType, parentDeviceAddress, parentDeviceType, key, value, istates):
        gaugename = key.lower() + "_set"
        logging.debug("Found enum param {} with value {}, gauge {}".format(key, value, gaugename))

        if value == '' or value is None:
            logging.debug("Skipping processing enum {} with empty value".format(key))
            return gaugename, None

        # this function is only executed in if blocks that filter for child devices (channels)
        # therefore, we can always assume that device corresponds to a channel and parent device to the actual device

        if not self.metrics.get(gaugename):
            self.metrics[gaugename] = Enum(gaugename, 'Metrics for ' + key, states=istates, labelnames=[
                'ccu',
                'channel_address',
                'channel_type',
                'channel_id',
                'channel_name',
                'channel_room_id',
                'channel_room_name',
                'channel_function_id',
                'channel_function_name',
                'device_address',
                'device_type',
                'device_id',
                'device_name',
                'device_room_id',
                'device_room_name',
                'device_function_id',
                'device_function_name'
            ], namespace=self.METRICS_NAMESPACE)
        gauge = self.metrics.get(gaugename)
        deviceMapping = self.resolve_device_mapping(deviceAddress, parentDeviceAddress)
        state = istates[int(value)]
        logging.debug("Setting {} to value {}/{}".format(deviceMapping.get("name"), str(value), state))
        gauge.labels(
            ccu=self.ccu_host,
            channel_address=deviceAddress,
            channel_type=deviceType,
            channel_id=deviceMapping.get("channel", {}).get("id"),
            channel_name=deviceMapping.get("channel", {}).get("name"),
            channel_room_id=deviceMapping.get("channel", {}).get("firstRoom", {}).get("id"),
            channel_room_name=deviceMapping.get("channel", {}).get("firstRoom", {}).get("name"),
            channel_function_id=deviceMapping.get("channel", {}).get("firstFunction", {}).get("id"),
            channel_function_name=deviceMapping.get("channel", {}).get("firstFunction", {}).get("name"),
            device_address=parentDeviceAddress,
            device_type=parentDeviceType,
            device_id=deviceMapping.get("id"),
            device_name=deviceMapping.get("name"),
            device_room_id=deviceMapping.get("mainRoom", {}).get("id"),
            device_room_name=deviceMapping.get("mainRoom", {}).get("name"),
            device_function_id=deviceMapping.get("mainFunction", {}).get("id"),
            device_function_name=deviceMapping.get("mainFunction", {}).get("name")
        ).state(state)

        return gaugename, (
            str(self.ccu_host),
            str(deviceAddress),
            str(deviceType),
            str(deviceMapping.get("channel", {}).get("id")),
            str(deviceMapping.get("channel", {}).get("name")),
            str(deviceMapping.get("channel", {}).get("firstRoom", {}).get("id")),
            str(deviceMapping.get("channel", {}).get("firstRoom", {}).get("name")),
            str(deviceMapping.get("channel", {}).get("firstFunction", {}).get("id")),
            str(deviceMapping.get("channel", {}).get("firstFunction", {}).get("name")),
            str(parentDeviceAddress),
            str(parentDeviceType),
            str(deviceMapping.get("id")),
            str(deviceMapping.get("name")),
            str(deviceMapping.get("mainRoom", {}).get("id")),
            str(deviceMapping.get("mainRoom", {}).get("name")),
            str(deviceMapping.get("mainFunction", {}).get("id")),
            str(deviceMapping.get("mainFunction", {}).get("name"))
        )

    def read_device_mapping(self):
        """Reads device mappings via CCU TCL script, returns a dict of device address to device id, name, rooms and functions"""
        url = "http://{}:8181/tclrega.exe".format(self.ccu_host)

        # this script returns the UI names of all devices (D), channels (C), rooms (R) and functions (F).
        # one entry per line, tab separated the object type, device address, channel address, UI object name and object ID.
        # inspired by https://github.com/mdzio/ccu-historian/blob/master/hc-utils/src/mdz/hc/itf/hm/HmScriptClient.groovy
        script_get_device_mappings = """
      string id;
      foreach(id, root.Devices().EnumIDs()) {
        var device=dom.GetObject(id);
        if (device.ReadyConfig()==true && device.Name()!='Gateway') {
          WriteLine("D\t" # device.Address() # "\t\t" # device.Name() # "\t" # id);
          if (device.Type()==OT_DEVICE) {
            string chId;
            foreach(chId, device.Channels()) {
              var ch=dom.GetObject(chId);
              WriteLine("C\t" # device.Address() # "\t" # ch.Address() # "\t" # ch.Name() # "\t" # chId);
              string rmId;
              foreach(rmId, ch.ChnRoom()) {
                var rm = dom.GetObject(rmId);
                WriteLine("R\t" # device.Address() # "\t" # ch.Address() # "\t" # rm.Name() # "\t" # rmId);
              }
              string fnId;
              foreach(fnId, ch.ChnFunction()) {
                var fn = dom.GetObject(fnId);
                WriteLine("F\t" # device.Address() # "\t" # ch.Address() # "\t" # fn.Name() # "\t" # fnId);
              }
            }
          }
        }
      }
      """

        response = requests.post(url, auth=self.auth, data=script_get_device_mappings)
        logging.debug(response.text)
        if response.status_code != 200:
            logging.warning("Failed to read device mappings, status code was %d", response.status_code)
            return {}

        devices = []
        channels = []
        rooms = []
        functions = []

        # parse the returned lines
        lines = response.text.splitlines()
        for line in lines:

            # ignore last line that starts with <xml><exec>
            if line.startswith("<xml><exec>"):
                continue

            (objType, deviceAddress, channelAddress, objName, objId) = line.split("\t")

            if objType == "D":
                devices += [{
                    "id": objId,
                    "name": objName,
                    "address": deviceAddress
                }]
            elif objType == "C":
                channels += [{
                    "id": objId,
                    "name": objName,
                    "address": channelAddress,
                    "deviceAddress": deviceAddress
                }]
            elif objType == "R":
                rooms += [{
                    "id": objId,
                    "name": objName,
                    "channelAddress": channelAddress,
                    "deviceAddress": deviceAddress
                }]
            elif objType == "F":
                functions += [{
                    "id": objId,
                    "name": objName,
                    "channelAddress": channelAddress,
                    "deviceAddress": deviceAddress
                }]

        # build hierarchical dictionary from individual devices, channels, rooms, and functions dictionaries
        ccu_device_mappings = {
            device["address"] : {k: v for k, v in device.items() if k != "address"} | {
                "channels": {
                    channel["address"]: {k: v for k, v in channel.items() if k not in ("address", "deviceAddress")} | {
                        "rooms": {
                            room["id"]: {k: v for k, v in room.items() if k not in ("id", "channelAddress", "deviceAddress")}
                            for room in rooms if room["deviceAddress"] == device["address"]
                            and room["channelAddress"] == channel["address"]
                        },
                        "functions": {
                            function["id"]: {k: v for k, v in function.items() if k not in ("id", "channelAddress", "deviceAddress")}
                            for function in functions if function["deviceAddress"] == device["address"]
                            and function["channelAddress"] == channel["address"]
                        }
                    } for channel in channels if channel["deviceAddress"] == device["address"]
                },
            } for device in devices
        }

        # add firstRoom and firstFunction for each channel
        # aggregate rooms and function on device level
        # add roomIds and functionIds lists on device level as intermediate step
        ccu_device_mappings = {
            deviceAddress: {k: v for k, v in deviceAttrs.items() if k != "channels"} | {
                "channels": {
                    channelAddress: channelAttrs | {
                        "firstRoom": {"id" : next(iter(channelAttrs["rooms"].keys()))} | \
                                next(iter(channelAttrs["rooms"].values())) \
                                if len(channelAttrs["rooms"]) > 0 else {},
                        "firstFunction": {"id" : next(iter(channelAttrs["functions"].keys()))} | \
                                next(iter(channelAttrs["functions"].values())) \
                                if len(channelAttrs["functions"]) > 0 else {}
                    } for channelAddress, channelAttrs in deviceAttrs["channels"].items()
                },
                "rooms": {
                    roomId: roomAttrs for channelAttrs in deviceAttrs["channels"].values()
                    for roomId, roomAttrs in channelAttrs["rooms"].items()
                },
                "functions": {
                    functionId: functionAttrs for channelAttrs in deviceAttrs["channels"].values()
                    for functionId, functionAttrs in channelAttrs["functions"].items()
                },
                "roomIds": [ # list is only an intermediate step
                    roomId for channelAttrs in deviceAttrs["channels"].values()
                    for roomId in channelAttrs["rooms"].keys()
                ],
                "functionIds": [ # list is only an intermediate step
                    functionId for channelAttrs in deviceAttrs["channels"].values()
                    for functionId in channelAttrs["functions"].keys()
                ],
            } for deviceAddress, deviceAttrs in ccu_device_mappings.items()
        }

        # add mainRoomId and mainFunctionId on device level as intermediate step
        # remove roomIds and functionIds lists from previous intermediate step
        ccu_device_mappings = {
            deviceAddress: {k: v for k, v in deviceAttrs.items() if k not in ("roomIds", "functionIds")} | {
                "mainRoomId": max(set(deviceAttrs["roomIds"]), key = deviceAttrs["roomIds"].count) \
                        if len(deviceAttrs["roomIds"]) > 0 else None,
                "mainFunctionId": max(set(deviceAttrs["functionIds"]), key = deviceAttrs["functionIds"].count) \
                        if len(deviceAttrs["functionIds"]) > 0 else None
            } for deviceAddress, deviceAttrs in ccu_device_mappings.items()
        }

        # add mainRoom and mainFunction on device level
        # remove mainRoomId and mainFunctionId from previous intermediate step
        ccu_device_mappings = {
            deviceAddress: {k: v for k, v in deviceAttrs.items() if k not in ("mainRoomId", "mainFunctionId")} | {
                "mainRoom": {"id": deviceAttrs["mainRoomId"]} | \
                        deviceAttrs["rooms"][deviceAttrs["mainRoomId"]] \
                        if deviceAttrs["mainRoomId"] != None else {},
                "mainFunction": {"id": deviceAttrs["mainFunctionId"]} | \
                        deviceAttrs["functions"][deviceAttrs["mainFunctionId"]] \
                        if deviceAttrs["mainFunctionId"] != None else {}
            } for deviceAddress, deviceAttrs in ccu_device_mappings.items()
        }

        return ccu_device_mappings

class _ThreadingSimpleServer(ThreadingMixIn, HTTPServer):
    """Thread per request HTTP server."""


if __name__ == '__main__':

    PARSER = argparse.ArgumentParser()
    PARSER.add_argument("--ccu_host", help="The hostname of the ccu instance", required=True)
    PARSER.add_argument("--ccu_port", help="The port for the xmlrpc service (2001 for BidcosRF, 2010 for HmIP)", default=2010)
    PARSER.add_argument("--ccu_user", help="The username for the CCU (if authentication is enabled)")
    PARSER.add_argument("--ccu_pass", help="The password for the CCU (if authentication is enabled)")
    PARSER.add_argument("--interval", help="The interval between two gathering runs in seconds", default=60)
    PARSER.add_argument("--mappingreload", help="After how many intervals the device mappings are reloaded", default=30)
    PARSER.add_argument("--port", help="The port where to expose the exporter", default=8010)
    PARSER.add_argument("--config_file", help="A config file with e.g. supported types and device mappings")
    PARSER.add_argument("--debug", action="store_true")
    PARSER.add_argument("--dump_devices", help="Do not start exporter, just dump device list", action="store_true")
    PARSER.add_argument("--dump_parameters", help="Do not start exporter, just dump device parameters of given device")
    PARSER.add_argument("--dump_device_mappings", help="Do not start exporter, just dump device mappings", action="store_true")
    ARGS = PARSER.parse_args()

    if ARGS.debug:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    auth = None
    if ARGS.ccu_user and ARGS.ccu_pass:
        auth = (ARGS.ccu_user, ARGS.ccu_pass)

    PROCESSOR = HomematicMetricsProcessor(ARGS.ccu_host, ARGS.ccu_port, auth, ARGS.interval, ARGS.mappingreload, ARGS.config_file)

    if ARGS.dump_devices:
        print(pformat(PROCESSOR.fetch_devices_list()))
    elif ARGS.dump_parameters:
        #    print("getParamsetDescription:")
        #    print(pformat(PROCESSOR.fetch_param_set_description(ARGS.dump_parameters)))
        print("getParamset:")
        print(pformat(PROCESSOR.fetch_param_set(ARGS.dump_parameters)))
    elif ARGS.dump_device_mappings:
        print(pformat(PROCESSOR.read_device_mapping()))
    else:
        PROCESSOR.start()
        # Start up the server to expose the metrics.
        logging.info("Exposing metrics on port {}".format(ARGS.port))
        start_http_server(int(ARGS.port))
        # Wait until the main loop terminates
        PROCESSOR.join()
