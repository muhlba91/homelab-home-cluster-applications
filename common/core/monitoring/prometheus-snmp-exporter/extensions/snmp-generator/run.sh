#!/bin/sh

# clean up old MIBs and create directory for new ones
rm -rf mibs
mkdir -p mibs

# Standard MIBs
wget -P mibs/ https://raw.githubusercontent.com/librenms/librenms/master/mibs/SNMPv2-SMI
wget -P mibs/ https://raw.githubusercontent.com/librenms/librenms/master/mibs/SNMPv2-TC
wget -P mibs/ https://raw.githubusercontent.com/librenms/librenms/master/mibs/INET-ADDRESS-MIB
wget -P mibs/ https://raw.githubusercontent.com/librenms/librenms/master/mibs/SNMPv2-MIB
wget -P mibs/ https://raw.githubusercontent.com/librenms/librenms/master/mibs/IF-MIB
wget -P mibs/ https://raw.githubusercontent.com/librenms/librenms/master/mibs/IANAifType-MIB
wget -P mibs/ https://raw.githubusercontent.com/librenms/librenms/master/mibs/HOST-RESOURCES-MIB
wget -P mibs/ https://raw.githubusercontent.com/librenms/librenms/master/mibs/HOST-RESOURCES-TYPES

# MikroTik MIBs
wget -P mibs/ https://download.mikrotik.com/routeros/7.22.1/mikrotik.mib

# generate snmp.yml
podman run --rm -it -v "${PWD}:/opt/" prom/snmp-generator generate
