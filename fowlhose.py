#!/usr/bin/env -S python -u
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

* Usage
  ./fowlhose.py set-rule doggos "puppy has:images"
  ./fowlhose.py set-rule kitties "kittie has:images"
  ./fowlhose.py list-rules
  ./fowlhose.py stream > rainy_day_pics.json
"""
import asyncio
from base64 import b64encode
from collections import OrderedDict
import json
import logging
import os
import sys
from typing import Any, Dict, IO, List, Optional
from urllib.parse import quote as urlquote

__description__ = "Filter and follow the Twitter Stream"
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


def print_ascii_table(
        data: List[List[Any]],
        headers: Optional[List[str]] = None,
        stream: IO = sys.stdout):
    """Prints an ASCII table from data in a dictionary.

    .. note::

       Order will matter. Take care to make sure the order of your column data
       doesn't change from row to row (or header and color).

    Args:
        data: Each sub-list in ``data`` will be a row in the table. Each
            element in the sub-list will be a column.
        header: Each element in the list will be used as a header.
        stream: The stream to write the table to.
    Raises:
        ValueError: header length and column count mismatch or column count
            differences.
    """
    num_columns = None
    col_max_lengths = {}

    # ###
    # Calculate the max length of the column
    for i, row in enumerate(data):
        if num_columns is None:
            num_columns = len(row)
            if headers and len(headers) != num_columns:
                raise ValueError(
                    "Missing header values: (row[{}]){} != {}(headers)".format(
                        i, len(row), len(headers)))

        if num_columns != len(row):
            raise ValueError(
                "Column length mismatch. All columns should be the same "
                "length. Calculated {} row[{}] has {}".format(
                    num_columns, i, len(row)))

        for j, column in enumerate(row):
            col_len = len(column)
            if j not in col_max_lengths:
                col_max_lengths[j] = col_len
                continue
            elif col_max_lengths[j] < col_len:
                col_max_lengths[j] = col_len

    if headers:
        # Are any of the headers longer than the
        for i, header in enumerate(headers):
            header_len = len(header)
            if i not in col_max_lengths:
                col_max_lengths[i] = header_len
            elif col_max_lengths[i] < header_len:
                col_max_lengths[i] = header_len

    #   total of col_max_lengths
    # + (the number of columns +1(for the last pipe and space)
    #    * 2 (one for the pipe and space added)
    # This will work if there is a leading and trailing space to "round" the
    # corners
    ruler_length = sum(col_max_lengths.values()) + ((len(col_max_lengths) + 1) * 2)

    stream.write(" {}\n".format("-"*ruler_length))
    if headers:
        # start of the header row
        stream.write("|")
        for i, header in enumerate(headers):
            stream.write(" {v:<{l}} |".format(v=header, l=col_max_lengths[i]))
        stream.write("\n")
        # Separator ruler
        stream.write("|")
        for i, header in enumerate(headers):
            # Add 2 for the added space and pipe
            stream.write("{v}|".format(v="="*(col_max_lengths[i] + 2)))
        stream.write("\n")

    for row in data:
        stream.write("|")
        for i, column in enumerate(row):
            stream.write(" {v:<{l}} |".format(v=column, l=col_max_lengths[i]))
        stream.write("\n")
    stream.write(" {}\n".format("-"*ruler_length))


async def _oauth_get_bearer_token(
        client: aiohttp.ClientSession, access_token: str,
        secret_token: str) -> str:
    """Retrieve a valid bearer token from Twitter.

    .. note::

       This function is not asynchronous because there is nothing we can be
       doing before authentication.

    Args:
        client: HTTP client to use for the request.
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


async def list_filter_rules(client: aiohttp.ClientSession) -> List[Dict[str, str]]:
    """Get all filter rules for this account.

    Args:
        client: HTTP client to use for the request.

    Return:
        A list of rules
        [{'id': '<rule_id>', 'value': '<rule>', 'name': '<name>'}, ]
    """
    data = None  # Response from API
    ret = []  # Return value

    async with client.get(RULES_URL) as response:
        logger.debug("GET {} -> {} {}".format(
            RULES_URL, response.status, response.reason))
        data = await response.json()
        logger.debug("{}".format(data))

    if data and "data" in data:
        for rule in data["data"]:
            ret.append({
                "id": rule["id"],
                "value": rule["value"],
                "name": rule["tag"]})

    return tuple(ret)


async def add_filter_rule(
        client: aiohttp.ClientSession,
        name: str,
        rule: str) -> bool:
    """Create a filter rule.

    .. seealso:

       For the filter rule syntax, see Twitters documentation.
       https://developer.twitter.com/en/docs/labs/filtered-stream/guides/search-queries

    Args:
        client: HTTP client to use for the request.
        name: the name of the rule
        rule: the filter to create

    Return:
        ``True`` if the rule creation was successful
    """

    payload = {
        "add": [
            {"value": rule, "tag": name},
        ]
    }
    async with client.post(RULES_URL, json=payload) as response:
        logger.debug("POST {} -> {} {}".format(
            RULES_URL, response.status, response.reason))
        data = await response.json()
        logger.debug(data)
    return data["meta"]["summary"]["created"] == 1


async def reset_filter_rules(client: aiohttp.ClientSession) -> bool:
    """Remove all filters.

    Args:
        client: HTTP client to use for the request.

    Returns:
        ``True`` if _all_ rules were deleted. ``False`` if one or more rules
        were not deleted.
    """
    rules = await list_filter_rules(client)

    if not rules:
        return True

    ids = tuple(d["id"] for d in rules)
    return await delete_filter_rules(client, ids)


async def delete_filter_rules(client: aiohttp.ClientSession, ids: List[int]) -> bool:
    """Remove a list of ids from the ruleset.

    Args:
        client: HTTP client to use for the request.
        ids: A list of ids to remove
    Returns:
        ``True`` if _all_ rules were deleted. ``False`` if one or more rules
        were not deleted.
    """
    payload = {
        "delete": {
            "ids": ids,
        }
    }
    async with client.post(RULES_URL, json=payload) as response:
        logger.debug("POST {} -> {} {}".format(
            RULES_URL, response.status, response.reason))
        data = await response.json()
        logger.debug(data)

    return data["meta"]["summary"]["not_deleted"] == 0\


async def delete_filter_rule(client: aiohttp.ClientSession, id_: int) -> bool:
    """Remove the provided id from the ruleset

    Args:
        client: HTTP client to use for the request.
        id_: The rule id to remove
    Returns:
        ``True`` if _all_ rules were deleted. ``False`` if one or more rules
        were not deleted.
    """
    ids = [id_, ]
    return await delete_filter_rules(client, ids)


async def create_client(access_token: str, secret_token: str) -> aiohttp.ClientSession:
    """Get an authenticated http client ready to make Twitter API requests.

    Args:
        access_token: Your app's access token from developer.twitter.com
        secret_token: Your app's secret token from developer.twitter.com
    """
    client = aiohttp.ClientSession()
    bearer_token = await _oauth_get_bearer_token(
        client, access_token, secret_token)

    client._default_headers.extend({
        "Authorization": "Bearer {}".format(bearer_token)
    })

    return client


async def connect_stream(client: aiohttp.ClientSession):

    # Disable the timeout for streaming
    timeout = aiohttp.ClientTimeout(total=None)
    async with client.get(STREAM_URL, timeout=timeout) as response:
        logger.debug("GET {} -> {} {}".format(
            STREAM_URL, response.status, response.reason))

        if response.status == 200:
            async for tweet in response.content:
                tweet = tweet.decode("utf-8").strip()
                if not tweet: continue
                yield tweet


async def stream_tweets(access_token: str, secret_token: str):
    """Utility method that performs the setup to stream filtered tweets.

    Args:
        access_token: Your app's access token from developer.twitter.com
        secret_token: Your app's secret token from developer.twitter.com
    """
    client = await create_client(access_token, secret_token)
    try:
        async for tweet in connect_stream(client):
            yield tweet
    except:
        # TODO: Signal cleanup -- CTRL+c leaves the client open.
        logger.info("Closing client...")
        await client.close()


if __name__ == "__main__":
    import argparse

    TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
    TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET")

    logger.setLevel(logging.DEBUG)

    async def cmd_list_rules(args: argparse.Namespace):
        try:
            client = await create_client(TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET)
            rules = await list_filter_rules(client)

            if True:  # if human readable
                header = ("id", "name", "value")
                rows = tuple(
                    [r[header[0]], r[header[1]], r[header[2]]] for r in rules)
                print_ascii_table(rows, header)
            else:  # if machine readable
                sys.stdout.write("{}\n", json.dumps(rules))

        finally:
            await client.close()

    # async def cmd_get_rule(args: argparse.Namespace):
    #     try:
    #         client = await create_client(
    #             TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET)
    #         r = await add_filter_rule(client, args.name, args.value)
    #     finally:
    #         await client.close()

    async def cmd_set_rule(args: argparse.Namespace):
        try:
            client = await create_client(
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET)
            r = await add_filter_rule(client, args.name, args.value)
        finally:
            await client.close()

        if r:
            logger.info("Successfully added rule")
        else:
            logger.error("Unable to add rule")

    async def cmd_reset_rules(args: argparse.Namespace):
        try:
            client = await create_client(
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET)
            r = await reset_filter_rules(client)
        finally:
            await client.close()

        if r:
            logger.info("Successfully reset rules")
        else:
            logger.error("One or more rules were not removed during reset")


    async def cmd_delete_rule(args: argparse.Namespace):
        try:
            client = await create_client(
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET)
            r = await delete_filter_rule(client, args.id)
        finally:
            await client.close()

        if r:
            logger.info("Successfully removed rule {}".format(args.id))
        else:
            logger.error("Unable to remove rule {}".format(args.id))

    async def cmd_stream(args: argparse.Namespace):
        async for tweet in stream_tweets(
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET):
            sys.stdout.write("{}\n".format(tweet))

    parser = argparse.ArgumentParser(description=__description__)
    subparser = parser.add_subparsers(dest="command")

    list_rules_parser = subparser.add_parser(
        "list-rules", help="list your filter rules")
    list_rules_parser.set_defaults(func=cmd_list_rules)

    # get_rule_parser = subparser.add_parser(
    #     "get-rule", help="get a filter rule by id")
    # get_rule_parser.add_argument("id", type=int, help="rule id to retrieve")

    set_rule_parser = subparser.add_parser(
        "set-rule", help="define a new filter rule.")
    set_rule_parser.add_argument(
        "name", type=str, help="a name to identify this rule")
    set_rule_parser.add_argument("value", type=str, help="the rule")
    set_rule_parser.set_defaults(func=cmd_set_rule)

    reset_rules_parser = subparser.add_parser(
        "reset-rules", help="reset all filter rules")
    reset_rules_parser.set_defaults(func=cmd_reset_rules)

    delete_rule_parser = subparser.add_parser(
        "delete-rule", help="delete a filter rule by id")
    delete_rule_parser.add_argument("id", type=int, help="rule to delete")
    delete_rule_parser.set_defaults(func=cmd_delete_rule)

    stream_parser = subparser.add_parser(
        "stream", help="follow the twitterverse with your filter rules")
    stream_parser.set_defaults(func=cmd_stream)

    args = parser.parse_args()

    sys.stderr.write("\n")
    sys.stderr.write(
        "      \u001b[38;5;8m- (\u001b[0m@$*&\u001b[38;5;8m)\u001b[0m\n")
    sys.stderr.write(
        "\u001b[38;5;33m _   \u001b[38;5;8m/\u001b[0m\n")
    sys.stderr.write(
        "\u001b[38;5;33m(\u001b[38;5;12m@\u001b[38;5;33m)"
        "\u001b[38;5;3m<\u001b[0m     Fowlhose - {}\n".format(__description__))
    sys.stderr.write(
        "\u001b[38;5;33m/-\\\u001b[0m      Version: {}\n\n".format(
            __version__))

    logger.debug("TWITTER_ACCESS_TOKEN: xxxxx{}".format(
        TWITTER_ACCESS_TOKEN[-7:]))
    logger.debug("TWITTER_ACCESS_SECRET: xxxxx{}".format(
        TWITTER_ACCESS_SECRET[-7:]))

    loop = asyncio.get_event_loop()
    loop.run_until_complete(args.func(args))
    loop.close()
