"""Notifier — multi-channel alert delivery.

Channels: console, telegram, webhook, sms.
All channels are optional — enable via config/notifiers.yaml.
"""
from notifier.manager import NotifierManager

__all__ = ["NotifierManager"]
