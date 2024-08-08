# Duplicati Home Assistant Integration

[![GitHub Release](https://img.shields.io/github/release/txxa/hass-duplicati.svg?style=for-the-badge)](https://github.com/txxa/hass-duplicati/releases)
[![GitHub Activity](https://img.shields.io/github/commit-activity/y/txxa/hass-duplicati.svg?style=for-the-badge)](https://github.com/txxa/hass-duplicati/commits/main)
[![License](https://img.shields.io/github/license/txxa/hass-duplicati.svg?style=for-the-badge)](LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://hacs.xyz/docs/faq/custom_repositories)

_Integration to interact with Duplicati backup software._

[Duplicati](https://www.duplicati.com) is a free backup client that securely stores encrypted, incremental, compressed backups on cloud storage services and remote file servers. It works with Amazon Cloud Drive, Amazon S3, Windows Live SkyDrive, Google Drive, Rackspace Cloud Files, WebDAV, SSH, FTP, and many others.

This integration allows you to monitor and control your Duplicati backups from within Home Assistant.

## Features

- Monitor status, execution time, duration, and other metrics of Duplicati backups.
- Support for multiple Duplicati instances.
- Support for multiple backup jobs per Duplicati instance.
- SSL/TLS support.
- Create new backups on demand (via button or service call).
- Refresh sensor data on demand (via button or service call).
- Event triggering for backup jobs (start, completion, fail).
- Configurable scan interval for sensor data updates.

## Installation

1. Add this repository as a custom repository to HACS: [![Add Repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=txxa&repository=hass-duplicati&category=integration)
2. Use HACS to install the integration.
3. Restart Home Assistant.
4. Set up the integration using the UI: [![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=duplicati)


## Development and maintenance

I basically created this integration for my personal purpose. As it fulfils all my current needs I won't develop it further for now.\
However, as long as I am using this integration in my Home Assistant setup I will maintain it actively.

## Contributions are welcome

If you want to contribute to this integration, please read the [Contribution guidelines](CONTRIBUTING.md)

### Providing translations for other languages

If you would like to use the integration in another language, you can help out by providing the necessary translations in [custom_components/duplicati/translations/](./custom_components/duplicati/translations/) and open a pull request with the changes.
