import os

SNAPSHOTS_PATH = "/snapshots"
INITIAL_QUOTA = int(os.environ['BN_CONSENSUS_SEED_GROUPS_INITIAL_QUOTA'])
MONTHLY_QUOTA = int(os.environ['BN_CONSENSUS_SEED_GROUPS_MONTHLY_QUOTA'])
BRIGHTID_CALLS = os.environ['BN_CONSENSUS_BRIGHTID_CALLS_GROUPS'].split(',')
