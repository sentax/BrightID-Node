import os
import socket
import json
import time
import shutil
import traceback
from arango import ArangoClient
from hashlib import sha256
import base64
import config
import verifications

db = ArangoClient(hosts=config.ARANGO_SERVER).db('_system')
variables = db.collection('variables')
verifiers = {
    'Seed': {'verifier': verifications.seed, 'step': 1},
    'SeedConnected': {'verifier': verifications.seed_connected, 'step': 1},
    # 'SeedConnectedWithFriend': {'verifier': verifications.seed_connected_with_friend, 'step': 1},
    # 'Yekta': {'verifier': verifications.yekta, 'step': 10},
    'BrightID': {'verifier': verifications.brightid, 'step': 1},
    # 'DollarForEveryone': {'verifier': verifications.dollar_for_everyone, 'step': 1},
    # 'SocialRecoverySetup': {'verifier': verifications.social_recovery_setup, 'step': 1},
    'predefined': {'verifier': verifications.predefined, 'step': 1},
    'apps': {'verifier': verifications.apps, 'step': 1},
}


def update_verifications_hashes(block):
    new_hashes = {}
    for v in verifiers:
        if block % (config.SNAPSHOTS_PERIOD * verifiers[v]['step']) != 0 or v == 'apps':
            continue
        # verifications = db['verifications'].find({'name': v, 'block': block})
        verifications = db['verifications'].find({'name': v})
        hashes = [v.get('hash', '') for v in verifications]
        message = ''.join(sorted(hashes)).encode('ascii')
        h = base64.b64encode(sha256(message).digest()).decode("ascii")
        new_hashes[v] = h.replace(
            '/', '_').replace('+', '-').replace('=', '')

    # store hashes for only last 2 blocks
    hashes = variables.get('VERIFICATIONS_HASHES')['hashes']
    hashes = json.loads(hashes)
    # json save keys (block numbers) as strings
    last_block = str(max(map(int, hashes.keys())))
    hashes = {block: new_hashes, last_block: hashes[last_block]}
    variables.update({
        '_key': 'VERIFICATIONS_HASHES',
        'hashes': json.dumps(hashes)
    })



def process():
    get_time = lambda: time.strftime('%Y-%m-%d %H:%M:%S')


    print(f'{get_time()} - processing scorer started...')
    
    block = db.collection('variables').get('LAST_BLOCK')['value']
    # If there are verifications for current block, it means there was
    # an error resulted in retrying the block. Remvoing these verifications
    # helps not filling database and preventing unknown problems that
    # having duplicate verifications for same block may result in
    db.aql.execute('''
        FOR v IN verifications
            FILTER  v.block == @block
            REMOVE { _key: v._key } IN verifications
        ''', bind_vars={'block': block})
    for v in verifiers:
        if block % (config.SNAPSHOTS_PERIOD * verifiers[v]['step']) != 0:
            continue
        verifiers[v]['verifier'].verify(block)

    update_verifications_hashes(block)
    fname = os.path.join(config.SNAPSHOTS_PATH, 'snapshot.chunk')

    # remove the snapshot file
    shutil.rmtree(fname, ignore_errors=True)
    print(f'{get_time()} - processing {fname} completed')


def next_snapshot():
    
    while True:
        fname = os.path.join(config.SNAPSHOTS_PATH, 'snapshot.chunk')
        if os.path.exists(fname):
            snapshot = 1
            return snapshot
        time.sleep(1)


def wait():
    while True:
        time.sleep(5)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(
            (config.BN_ARANGO_HOST, config.BN_ARANGO_PORT))
        sock.close()
        if result != 0:
            print('db is not running yet')
            continue
        # wait for ws to start upgrading foxx services and running setup script
        time.sleep(10)
        services = [service['name'] for service in db.foxx.services()]
        if 'apply' not in services or 'BrightID-Node' not in services:
            print('foxx services are not running yet')
            continue
        return


def main():
    print('waiting for db ...')
    wait()
    print('db started')
    while True:
        snapshot = next_snapshot()
        try:
            process(snapshot)
        except Exception as e:
            print(f'Error: {e}')
            traceback.print_exc()
            time.sleep(10)


if __name__ == '__main__':
    main()
