# Duplicati Home Assistant Integration

[![GitHub Release](https://img.shields.io/github/release/txxa/hass-duplicati.svg?style=for-the-badge)](https://github.com/txxa/hass-duplicati/releases)
[![GitHub Activity](https://img.shields.io/github/commit-activity/y/txxa/hass-duplicati.svg?style=for-the-badge)](https://github.com/txxa/hass-duplicati/commits/main)
[![License](https://img.shields.io/github/license/txxa/hass-duplicati.svg?style=for-the-badge)](https://github.com/txxa/hass-duplicati/blob/main/LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://hacs.xyz/docs/faq/custom_repositories)

_Integration to interact with Duplicati backup software._

[Duplicati](https://www.duplicati.com) is a free backup client that securely stores encrypted, incremental, compressed backups on cloud storage services and remote file servers. It works with Amazon Cloud Drive, Amazon S3, Windows Live SkyDrive, Google Drive, Rackspace Cloud Files, WebDAV, SSH, FTP, and many others.

This integration allows you to monitor and control your Duplicati backups from within Home Assistant.

## Table of contents

- [Features](#features)
- [Installation](#installation)
- [Configuration](#configuration)
- [Compatibility](#compatibility)
- [Development and Maintenance](#development-and-maintenance)
- [Contributions](#contributions)

## Features

- Monitor status, execution time, duration, and other metrics of Duplicati backups.
- Support for multiple Duplicati instances.
- Support for multiple backup jobs per Duplicati instance.
- Web interface password protection support.
- SSL/TLS support.
- Create new backups on demand (via button or service call).
- Refresh sensor data on demand (via button or service call).
- Event triggering for backup jobs (start, completion, fail).
- Dynamic backup management.
- Configurable scan interval for sensor data updates.

## Installation

1. Add this repository as a custom repository to HACS: [![Add Repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=txxa&repository=hass-duplicati&category=integration)
2. Use HACS to install the integration.
3. Restart Home Assistant.
4. Set up the integration using the UI: [![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=duplicati)


## Configuration

Configuration is done through the Home Assistant UI.

### Initial Setup

To add the integration, go to Settings ➤ Devices & Services ➤ Integrations, click ➕ Add Integration, and search for "Duplicati".

The initial setup needs the following information:

- **Backup server URL**: The URL of the Duplicati server.
- **Password**: The password for the Duplicati web interface.
- **Verify SSL certificate**: Whether to verify the SSL certificate of the Duplicati server.
- **Available backups**: The backups available on the Duplicati server.

### Options

Find configuration options under Settings ➤ Devices & Services ➤ Integrations ➤ Duplicati ➤ Configure.

The following options can be configured after the initial setup:

- **Backups to monitor**: The backups to monitor.
- **Scan interval [s]**: The interval at which to scan for new backup jobs.

## Compatibility

This integration has been tested with specific versions of Duplicati. To ensure proper functionality, please use the version of this integration that corresponds to your installed Duplicati version.

Refer to the compatibility matrix below:

| Integration | Duplicati               |
| :---------- | :---------------------- |
| v0.1.0      | 2.0.7.1_beta_2023-05-25 |
| v0.2.x      | 2.0.8.1_beta_2024-05-07 |
| v0.3.x      | 2.1.0.2_beta_2024-11-29<br>2.1.0.5_stable_2025-03-04 |

**Important notes:**

- The integration may work with versions of Duplicati not listed here, but these combinations have not been explicitly tested.
- It is recommended to use the most recent integration version compatible with your Duplicati version.
- If you encounter any issues with untested version combinations, please report them in the [Issues](../../issues) section of this repository.

For the best experience, try to keep both the integration and Duplicati updated to their latest compatible versions.

## Development and maintenance

I basically created this integration for my personal purpose. As it fulfils all my current needs I won't develop it further for now.\
However, as long as I am using this integration in my Home Assistant setup I will maintain it actively.

## Contributions

If you want to contribute to this integration, please read the [Contribution guidelines](CONTRIBUTING.md)

### Providing translations for other languages

If you would like to use the integration in another language, you can help out by providing the necessary translations in [custom_components/duplicati/translations/](./custom_components/duplicati/translations/) and open a pull request with the changes.
