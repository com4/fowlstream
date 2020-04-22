#!/usr/bin/env python
#-*- fill-column: 79 -*-
"""Fowlhose - Stream tweets based on filter rules.

* Setup:
** Dependencies
   pip install 'aiohttp[speedups]'  # async http client

** Twitter
   - Register as a Twitter Developer: https://developer.twitter.com
   - Create an app: https://developer.twitter.com/en/apps/create
   - Enable Filtered Stream API: https://developer.twitter.com/en/account/labs

* Environment Variables
  - ``FOWLBIRD_LOG_FORMAT`` - If you want a different log format
  - ``TWITTER_ACCESS_TOKEN`` - API Key from app page
  - ``TWITTER_SECRET_KEY`` - API Secret Key from app page
"""
import asyncio
from base64 import b64encode
import logging
import os
import sys
from urllib.parse import quote as urlquote

__version__ = "0.1"

class ColorizedFormatter(logging.Formatter):
    COLOR_RESET = "\u001b[0m"

    @staticmethod
    def get_level_color(levelno):
        if os.getenv("DISABLE_COLOR", False):
            return COLOR_RESET
        elif levelno <= 10:
            # DEBUG
            return "\u001b[38;5;14m"
        elif levelno <= 20:
            # INFO
            return "\u001b[38;5;27m"
        elif levelno <= 30:
            # WARNING
            return "\u001b[38;5;214m"
        elif levelno <= 40:
            # ERROR
            return"\u001b[38;5;9m"
        else:
            # CRITICAL
            return "\u001b[38;5;124m"

    def format(self, record):
        record.levelname = "{}{:8}{}".format(
            self.get_level_color(record.levelno),
            record.levelname,
            self.COLOR_RESET)

        return super().format(record)

LOG_FORMAT = os.getenv(
    "FOWLBIRD_LOG_FORMAT",
    "%(levelname)s | %(asctime)s | %(name)s[%(process)s] | %(msg)s")
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(ColorizedFormatter(LOG_FORMAT))
logger = logging.getLogger("fowlhose")
logger.addHandler(handler)

try:
    import aiohttp
except ImportError:
    import sys
    logger.error("Can't find aiohttp. try pip install 'aiohttp[speedups]'")
    sys.exit(1)


BASE_URL = "https://api.twitter.com"
STREAM_URL = "{}/labs/1/tweets/stream/filter".format(BASE_URL)
RULES_URL = "{}/labs/1/tweets/stream/filter/rules".format(BASE_URL)


async def _oauth_get_bearer_token(
        client: aiohttp.ClientSession, access_token: str,
        secret_token: str) -> str:
    """Retrieve a valid bearer token from Twitter.

    .. note::

       This function is not asynchronous because there is nothing we can be
       doing before authentication.

    Args:
        client: The HTTP client to make requests with
        access_token: Your app's access token from developer.twitter.com
        secret_token: Your app's secret token from developer.twitter.com

    Return:
        bearer token
    """
    AUTH_URL = "{}/oauth2/token".format(BASE_URL)
    # To generate the Authorization header:
    # 1. URL encode the consumer key and consumer secret according to RFC
    # 1738. Note that at the time of writing, this will not actually change the
    # consumer key and secret, but this step should still be performed in case
    # the format of those values changes in the future.
    access_token = urlquote(access_token)
    secret_token = urlquote(secret_token)
    # 2. Concatenate the encoded consumer key, a colon character ":", and the
    # encoded consumer secret into a single string.
    access_and_secret_token = (
        "{}:{}".format(access_token, secret_token)).encode("ascii")
    # 3. Base64 encode the string from the previous step.
    auth_token = b64encode(access_and_secret_token).decode("ascii")
    bearer_token = None

    headers = {
        "Authorization": "Basic {}".format(auth_token),
    }
    body = {"grant_type": "client_credentials"}

    async with client.post(AUTH_URL, headers=headers, data=body) as response:
        logger.debug("POST {} -> {} {}".format(
            AUTH_URL, response.status, response.reason))
        if response.status == 200:
            body = await response.json()
            bearer_token = body["access_token"]
    return bearer_token


async def get_filter_rules(client: aiohttp.ClientSession) -> dict:
    ret = None
    async with client.get(RULES_URL) as response:
        ret = await response.json()
    return ret


async def set_filter_rule(client: aiohttp.ClientSession, name: str, rule: str):
    # https://developer.twitter.com/en/docs/labs/filtered-stream/guides/search-queries
    payload = {
        "add": [
            {"value": rule, "tag": name},
        ]
    }
    async with client.post(RULES_URL, json=payload) as response:
        logger.debug("POST {} -> {} {}".format(
            RULES_URL, response.status, response.reason))
        logger.debug(await response.json())


async def delete_all_filter_rules(client: aiohttp.ClientSession):
    rules = await get_filter_rules(client)

    if "data" not in rules:
        # No rules to delete
        return

    ids = list(d["id"] for d in rules["data"])
    payload = {
        "delete": {
            "ids": ids,
        }
    }
    async with client.post(RULES_URL, json=payload) as response:
        logger.debug("POST {} -> {} {}".format(
            RULES_URL, response.status, response.reason))


async def create_client(access_token: str, secret_token: str) -> aiohttp.ClientSession:
    """Get an authenticated http client ready to make Twitter API requests."""
    client = aiohttp.ClientSession()
    bearer_token = await _oauth_get_bearer_token(
        client, access_token, secret_token)

    client._default_headers.extend({
        "Authorization": "Bearer {}".format(bearer_token)
    })

    return client


async def connect_stream(client: aiohttp.ClientSession):
    async with client.get(STREAM_URL) as response:
        async for tweet in response.content:
            tweet = tweet.decode("utf-8")
            yield tweet


async def stream_tweets(access_token: str, secret_token: str):
    client = await create_client(access_token, secret_token)
    async for tweet in connect_stream(client):
        yield tweet

    # TODO: Signal cleanup -- CTRL+c leaves the client open.
    logger.info("Closing client...")
    await client.close()



async def _main(loop):

    TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
    TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET")

    logger.setLevel(logging.DEBUG)

    sys.stderr.write(
        "      \u001b[38;5;8m- (\u001b[0m@$*&\u001b[38;5;8m)\u001b[0m\n")
    sys.stderr.write(
        "\u001b[38;5;33m _   \u001b[38;5;8m/\u001b[0m\n")
    sys.stderr.write(
        "\u001b[38;5;33m(\u001b[38;5;12m@\u001b[38;5;33m)"
        "\u001b[38;5;3m<\u001b[0m     Fowlhose - Stream tweets based on "
        "filter rules\n")
    sys.stderr.write(
        "\u001b[38;5;33m/-\\\u001b[0m      Version: {}\n\n".format(
            __version__))

    logger.debug("FOWLBIRD_LOG_FORMAT: {}".format(LOG_FORMAT))
    logger.debug("TWITTER_ACCESS_TOKEN: xxxxx{}".format(
        TWITTER_ACCESS_TOKEN[-7:]))
    logger.debug("TWITTER_ACCESS_SECRET: xxxxx{}".format(
        TWITTER_ACCESS_SECRET[-7:]))

    async for tweet in stream_tweets(
            TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET):
        sys.stdout.write("{}\n".format(tweet.strip()))

    # client = await create_client(TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET)
    #
    # await delete_all_filter_rules(client)
    # await set_filter_rule(client, "covid-19", "covid-19")
    # rules = await get_filter_rules(client)
    # logger.debug("Rules: {}".format(rules))
    #
    # await client.close()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(_main(loop))
