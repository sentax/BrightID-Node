from arango import ArangoClient
import time

import utils
# import config

PENALTY = 3

# db = ArangoClient(hosts=config.ARANGO_SERVER).db("_system")
db = ArangoClient(hosts="http://localhost:8529").db("_system")


def seed_connections(members, after):
    return db.aql.execute(
        """
        FOR c in connectionsHistory
            FILTER c._from IN @members
                AND (c.timestamp > @after OR c.level == 'reported')
            SORT c.timestamp, c._from, c._to ASC
            RETURN c
    """,
        bind_vars={"after": after, "members": members},
    )


def last_verifications():
    cursor = db.aql.execute(
        """
        FOR v in verifications
            FILTER v.name == 'SeedConnected'
            RETURN v
    """
    )
    verifications = {v["user"]: v for v in cursor}
    return verifications


def fetch_memberships_history(group_id, after):
    return db.aql.execute(
        """
        FOR m in membershipsHistory
            FILTER m._to == @group_id
                AND m.timestamp > @after
            SORT m.timestamp ASC
            RETURN m
    """,
        bind_vars={"after": after, "group_id": group_id},
    )


def verify(block):
    print("SEED CONNECTED")
    # new code should not use snapshots.
    # we use history colls beside the snapshot colls ( connectionsHistory, membershipsHistory )
    
    counts = {}
    users = last_verifications()

    for u, v in users.items():
        v['reported'] = []
        # this if block used to init communities and can be removed in the next release
        if 'communities' not in v:
            v['communities'] = v.get('connected', [])
        for g in v.get('connected', []):
            counts[g] = counts.get(g, 0) + 1
            
 


    prev_snapshot_time = db["variables"].get("PREV_SNAPSHOT_TIME")["value"]
    seed_groups = db["groups"].find({"seed": True})

    for seed_group in seed_groups:
        seed_users = {}
        # print('group',seed_group)

        cursor = db["usersInGroups"].find({"_to": seed_group["_id"]})
        members = [ug["_from"] for ug in cursor]
        for member in range(len(members)):
            # print('member',members[member])
            if member not in seed_users:
                seed_users[members[member]] = {}
                seed_users[members[member]]["status"] = True
        memberships_history = []
        try:
            memberships_history = list(
                fetch_memberships_history(seed_group["_id"], prev_snapshot_time)
            )  #! change this 0
        except:
            memberships_history = []  # membershipHisotry does not exists
        _mc = 0
        # move backward to find correct seed users in that time
        # reverse the memberships_history
        memberships_history = memberships_history[::-1]
        for membership in memberships_history:
            if membership["_from"] not in seed_users:
                seed_users[membership["_from"]] = {}
                seed_users[membership["_from"]]["status"] = False
            if membership["type"] == "join":
                seed_users[membership["_from"]]["status"] = False
            elif membership["type"] == "leave":
                seed_users[membership["_from"]]["status"] = True

        memberships_history = memberships_history[::-1]
        # seeds are synced with prev_snapshot_time
        seed_users_id = list(seed_users.keys())

        # print(seed_users)
        # connections = seed_connections(seed_users_id, prev_snapshot_time * 1000)
        connections = seed_connections(seed_users_id, prev_snapshot_time)
        # print(list(connections))
        
        quota = seed_group.get("quota", 0)
        counter = counts.get(seed_group["_key"], 0)
        for c in connections:
            if (
                _mc < len(memberships_history)
                and "timestamp" in memberships_history[_mc]
            ):
                if (
                    memberships_history[_mc]['timestamp'] <= c['timestamp']
                    # memberships_history[_mc]["timestamp"]
                    # <= 1000000000000000000000000
                ):  # need to refresh seeds status
                    
                    seed = seed_users.get(memberships_history[_mc]["_from"])
                    # print(
                    #     seed, memberships_history[_mc],c, "Seeeeeeeeeeeeeeed sttttt/*/*"
                    # )
                    if memberships_history[_mc]["type"] == "join":
                        seed["status"] = True
                    elif memberships_history[_mc]["type"] == "leave":
                        seed["status"] = False
                    _mc += 1
            s = c["_from"]
            seed = seed_users.get(s)
            # print(seed)
            # check seed user validity
            if seed["status"] == False:
                print("rejecting seed user because of leave", seed)
                continue


            # main logic
            u = c["_to"].replace("users/", "")
            if u not in users:
                users[u] = {"connected": [], "reported": [], "communities": []}
            if c["level"] in ["just met", "already known", "recovery"]:
                if seed_group["_key"] not in users[u]["communities"]:
                    users[u]["communities"].append(seed_group["_key"])
                if seed_group["_key"] not in users[u]["connected"]:
                    counter += 1
                    if counter <= quota:
                        users[u]["connected"].append(seed_group["_key"])
            elif c["level"] == "reported":
                if seed_group["_key"] not in users[u]["reported"]:
                    users[u]["reported"].append(seed_group["_key"])

        spent = min(counter, quota)
        exceeded = max(counter - quota, 0)
        region = seed_group.get("region")
        print(f"{region}, quota: {quota}, spent: {spent}, exceeded: {exceeded}")

    counter = 0
    batch_db = db.begin_batch_execution(return_result=True)
    verifications_col = batch_db.collection("verifications")
    for u, d in users.items():
        # penalizing users that are reported by seeds
        rank = len(d["connected"]) - len(d["reported"]) * PENALTY
        if(users[u].get("rank") == rank):
            print('rank is the same, ignore',u, rank, d["connected"], d["reported"])
            continue
        elif(users[u].get("rank") != None):
            # needs to be updated
            print('updating rank',u)
            verifications_col.update(
                {
                    "_key": users[u]["_key"],
                    "name": "SeedConnected",
                    "user": u,
                    "rank": rank,
                    "connected": d["connected"],
                    "communities": d["communities"],
                    "reported": d["reported"],
                    "block": block,
                    "timestamp": int(time.time() * 1000),
                    "hash": utils.hash("SeedConnected", u, rank),
                }
            )
        else:
            print(rank,u,d["connected"],d["reported"])
            verifications_col.insert(
                {
                    "name": "SeedConnected",
                    "user": u,
                    "rank": rank,
                    "connected": d["connected"],
                    "communities": d["communities"],
                    "reported": d["reported"],
                    "block": block,
                    "timestamp": int(time.time() * 1000),
                    "hash": utils.hash("SeedConnected", u, rank),
                }
            )

        if rank > 0:
            counter += 1

        if counter % 1000 == 0:
            batch_db.commit()
            batch_db = db.begin_batch_execution(return_result=True)
            verifications_col = batch_db.collection("verifications")
    batch_db.commit()

    print(f"verifications: {counter}\n")


# test purpose
def setupTests():
    print('setting up tests')
    # create coll if not exists
    # if not db.has_collection("variables"):
    #     db.create_collection("variables")
    # if not db.has_collection("users"):
    #     db.create_collection("users")
    # if not db.has_collection("groups"):
    #     db.create_collection("groups")
    # if not db.has_collection("usersInGroups"):
    #     db.create_collection("usersInGroups")
    # if not db.has_collection("membershipsHistory"):
    #     db.create_collection("membershipsHistory")
    # if not db.has_collection("connectionsHistory"):
    #     db.create_collection("connectionsHistory")
    # if not db.has_collection("verifications"):
    #     db.create_collection("verifications")
    db["variables"].insert({"_key": "PREV_SNAPSHOT_TIME", "value": 10})
    # db["variables"].insert({"_key": "VERIFICATION_BLOCK", "value": 10})
    # seed users
    db["users"].insert({"_key": "u1"})  # seed
    db["users"].insert({"_key": "u2"})
    db["users"].insert({"_key": "u3"})
    db["users"].insert({"_key": "u4"})  # seed
    db["users"].insert({"_key": "u5"})
    db["users"].insert({"_key": "u6"})
    db["users"].insert({"_key": "u7"})  # seed
    db["users"].insert({"_key": "u8"})
    db["users"].insert({"_key": "u9"})
    db["users"].insert({"_key": "u10"})

    # seed groups
    db["groups"].insert({"_key": "g1", "seed": True, "quota": 1000})
    db["groups"].insert({"_key": "g2", "seed": True, "quota": 2000})

    # users in groups (seeds)
    db["usersInGroups"].insert({"_from": "users/u1", "_to": "groups/g1"})
    db["membershipsHistory"].insert(
        {"_to": "groups/g1", "_from": "users/u1", "type": "join", "timestamp": 10}
    )

    db["usersInGroups"].insert({"_from": "users/u4", "_to": "groups/g1"})
    db["membershipsHistory"].insert(
        {"_to": "groups/g1", "_from": "users/u4", "type": "join", "timestamp": 12}
    )

    db["usersInGroups"].insert({"_from": "users/u7", "_to": "groups/g2"})
    db["membershipsHistory"].insert(
        {"_to": "groups/g2", "_from": "users/u7", "type": "join", "timestamp": 15}
    )

    # connections
    db["connectionsHistory"].insert(
        {
            "_from": "users/u1",
            "_to": "users/u2",
            "level": "just met",
            "timestamp": 12,
        }
    )
    
def setupSecondTest():
    print('setting up second test')
    # leave
    db["membershipsHistory"].insert(
        {"_to": "groups/g1", "_from": "users/u4", "type": "leave", "timestamp": 17}
    )
    # connect u4 with u2
    # this should be prevented
    db["connectionsHistory"].insert(
        {
            "_from": "users/u4",
            "_to": "users/u5",
            "level": "just met",
            "timestamp": 18,
        }
    )
    # this should be updated
    db["connectionsHistory"].insert(
        {
            "_from": "users/u7",
            "_to": "users/u2",
            "level": "just met",
            "timestamp": 20,
        }
    )

def cleanupTests():
    db["variables"].delete("PREV_SNAPSHOT_TIME")
    # db["variables"].delete("VERIFICATION_BLOCK")
    db["users"].truncate()
    db["groups"].truncate()
    db["usersInGroups"].truncate()
    db["membershipsHistory"].truncate()
    db["verifications"].truncate()
    db["connectionsHistory"].truncate()


def test():
    # test purpose
    cleanupTests()
    setupTests()
    verify(11)
    db["variables"].delete("PREV_SNAPSHOT_TIME")
    db["variables"].insert({"_key": "PREV_SNAPSHOT_TIME", "value": 16})

    setupSecondTest()
    verify(12)
    # cleanupTests()


test()