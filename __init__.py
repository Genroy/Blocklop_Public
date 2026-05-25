# -*- coding: utf-8 -*-
"""Initialize Blocklop plugin for QGIS."""

# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load Blocklop class from file Blocklop and return an instance.
    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    from .Blocklop import Blocklop
    return Blocklop(iface)
