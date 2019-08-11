# homematic_exporter

A simple tool to export data from [HomematicIP](https://www.homematic.com/) devices for consumption by [Prometheus](https://prometheus.io/), written in Python 3.

`homematic_exporter will listen on a freely definable port and emit data in prometheus format which is periodically fetched via XML-RPC from CCU3. The
fetching period is configurable, too.

## Usage

The exporter can be run in different modes:
* with `--dump_devices`, only the device list will be dumped and the script terminates (used for debugging purposes)
* with `--dump_parameters <deviceAddress>`, the parameters of a single device are dumped and the script terminates (used for debugging purposes)
* without special arguments (only `ccu_host`is mandatory) data is continuously gathered and exposed via HTTP

```bash
> ./exporter.py --help                                                                                                                                                   [±master ●]
usage: exporter.py [-h] --ccu_host CCU_HOST [--ccu_port CCU_PORT]
                   [--interval INTERVAL] [--port PORT]
                   [--mapping_file MAPPING_FILE] [--debug] [--dump_devices]
                   [--dump_parameters DUMP_PARAMETERS]

optional arguments:
  -h, --help            show this help message and exit
  --ccu_host CCU_HOST   The hostname of the ccu instance
  --ccu_port CCU_PORT   The port for the xmlrpc service
  --interval INTERVAL   The interval between two gathering runs
  --port PORT           The port where to expose the exporter
  --mapping_file MAPPING_FILE
                        A file with mapping from addresses to names
  --debug
  --dump_devices        Do not start exporter, just dump device list
  --dump_parameters DUMP_PARAMETERS
                        Do not start exporter, just dump device parameters of
                        given device
```

Can be used via docker as well.
```bash

> $ docker run --rm sfudeus/homematic_exporter --help
usage: homematic_exporter [-h] --ccu_host CCU_HOST [--ccu_port CCU_PORT]
                          [--interval INTERVAL] [--port PORT]
                          [--mapping_file MAPPING_FILE] [--debug]
                          [--dump_devices] [--dump_parameters DUMP_PARAMETERS]

optional arguments:
  -h, --help            show this help message and exit
  --ccu_host CCU_HOST   The hostname of the ccu instance
  --ccu_port CCU_PORT   The port for the xmlrpc service
  --interval INTERVAL   The interval between two gathering runs
  --port PORT           The port where to expose the exporter
  --mapping_file MAPPING_FILE
                        A file with mapping from addresses to names
  --debug
  --dump_devices        Do not start exporter, just dump device list
  --dump_parameters DUMP_PARAMETERS
                        Do not start exporter, just dump device parameters of
                        given device
```

## Metrics

Metrics are all prefixed with `homematic_`, the remaining name is based on the parameter name within the device descriptor.
All metrics are equipped with labels for the `ccu` instance, the device address, device type and parent device type.
In addition a device mapping can be added with a `--mapping_file`. Device addresses can be mapped to custom names which are then usable as labels in e.g. Grafana.

## Restrictions

Only a statically defined list of device types is supported so far (since I could only test those). Currently these are:
* the weather station (`HmIP-SWO-PL`)
* the temperature and humidity sensor (`HmIP-STH`)

If you want support for more devices, you can easily extend them in the source code (most likely I will make that configurable) or wait for me to implement that. You can support that by donating the intended device :-).
Feel free to open issues for unsupported items.
