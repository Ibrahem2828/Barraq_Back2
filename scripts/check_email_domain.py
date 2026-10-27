#!/usr/bin/env python3
"""Check whether a sending domain is authenticated well enough to reach the inbox.

    python scripts/check_email_domain.py baraqapp.com --dkim-selector s1 --dkim-selector s2

Uses only the standard library and DNS-over-HTTPS (dns.google), so it runs the
same on a laptop, in CI and on the server. Exit status is 1 when SPF or DMARC is
missing or unusable, so it can gate a release.

What it can and cannot tell you: it reads the DNS records receivers consult. It
cannot know which DKIM selector your provider signs with (pass --dkim-selector;
the selector is in the DNS instructions your provider gives you, and in the
DKIM-Signature header of a delivered message), nor how a given mailbox provider
scores your sending reputation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request

DOH = 'https://dns.google/resolve'


def query(name: str, record_type: str) -> list[str]:
    url = f'{DOH}?{urllib.parse.urlencode({"name": name, "type": record_type})}'
    with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310 -- fixed https endpoint
        answer = json.load(response).get('Answer', [])
    wanted = {'TXT': 16, 'MX': 15, 'A': 1}[record_type]
    values = []
    for record in answer:
        if record.get('type') != wanted:
            continue
        data = record['data']
        # dns.google returns TXT data unquoted; other resolvers quote each 255-byte chunk.
        quoted = re.findall(r'"([^"]*)"', data)
        values.append(''.join(quoted) if record_type == 'TXT' and quoted else data)
    return values


def check_spf(domain: str) -> tuple[bool, str]:
    records = [v for v in query(domain, 'TXT') if v.lower().startswith('v=spf1')]
    if not records:
        return False, 'MISSING -- receivers cannot tell which servers may send for this domain'
    if len(records) > 1:
        return False, f'INVALID -- {len(records)} SPF records (there must be exactly one): {records}'
    record = records[0]
    lookups = len(re.findall(r'\b(include:|a\b|mx\b|ptr\b|exists:|redirect=)', record))
    if lookups > 10:
        return False, f'INVALID -- about {lookups} DNS lookups (limit is 10): {record}'
    if record.rstrip().endswith('+all') or re.search(r'\s\+all\b', record):
        return False, f'UNSAFE -- "+all" lets anyone send as this domain: {record}'
    if not re.search(r'[~-]all\b', record) and 'redirect=' not in record:
        return False, f'WEAK -- no "~all" or "-all" ending: {record}'
    return True, record


def check_dmarc(domain: str) -> tuple[bool, str]:
    records = [v for v in query(f'_dmarc.{domain}', 'TXT') if v.lower().startswith('v=dmarc1')]
    if not records:
        return False, 'MISSING -- Gmail and Yahoo require a DMARC policy from bulk senders'
    record = records[0]
    policy = re.search(r'\bp=(\w+)', record)
    if not policy:
        return False, f'INVALID -- no p= tag: {record}'
    note = ''
    if policy.group(1).lower() == 'none':
        note = ' (monitoring only: move to quarantine, then reject, once reports are clean)'
    if 'rua=' not in record:
        note += ' (no rua= address: you will receive no aggregate reports)'
    return True, record + note


def check_dkim(domain: str, selectors: list[str]) -> list[tuple[str, bool, str]]:
    results = []
    for selector in selectors:
        name = f'{selector}._domainkey.{domain}'
        records = [v for v in query(name, 'TXT') if 'p=' in v or v.lower().startswith('v=dkim1')]
        if not records:
            cnames = query(name, 'TXT')
            results.append((selector, False, 'MISSING' if not cnames else f'unrecognised: {cnames}'))
        else:
            key = re.search(r'\bp=([A-Za-z0-9+/=]+)', records[0])
            detail = f'present (public key is {len(key.group(1))} base64 characters; a 2048-bit key is about 392)' if key else 'present but has no public key'
            results.append((selector, bool(key and key.group(1)), detail))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('domain')
    parser.add_argument('--dkim-selector', action='append', default=[], dest='selectors')
    args = parser.parse_args()
    domain = args.domain.strip().lower()

    failures = 0
    mx = query(domain, 'MX')
    print(f'domain: {domain}')
    print(f'MX    : {"; ".join(mx) if mx else "none (the domain cannot receive replies or bounces)"}')

    ok, detail = check_spf(domain)
    print(f'SPF   : {"OK" if ok else "FAIL"} -- {detail}')
    failures += not ok

    ok, detail = check_dmarc(domain)
    print(f'DMARC : {"OK" if ok else "FAIL"} -- {detail}')
    failures += not ok

    if args.selectors:
        for selector, ok, detail in check_dkim(domain, args.selectors):
            print(f'DKIM  : {"OK" if ok else "FAIL"} -- selector "{selector}": {detail}')
            failures += not ok
    else:
        print('DKIM  : not checked (pass --dkim-selector <name> from your provider\'s DNS instructions)')

    print('\nverdict:', 'authenticated' if not failures else f'{failures} problem(s): mail from this domain is likely to be treated as suspicious')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
