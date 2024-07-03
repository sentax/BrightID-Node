from arango import ArangoClient
import time
from . import utils
import config

PENALTY = 3

# db = ArangoClient(hosts="http://localhost:8529").db("_system")
db = ArangoClient(hosts=config.ARANGO_SERVER).db('_system')


def seed_connections(members, after):
    query = """
        FOR c IN connectionsHistory
            FILTER c._from IN @members AND (c.timestamp > @after OR c.level == 'reported')
            SORT c.timestamp, c._from, c._to ASC
            RETURN c
    """
    return db.aql.execute(query, bind_vars={"after": after, "members": members})


def last_verifications():
    query = """
        FOR v IN verifications
            FILTER v.name == 'SeedConnected'
            RETURN v
    """
    cursor = db.aql.execute(query)
    return {v["user"]: v for v in cursor}


def fetch_memberships_history(group_id, after):
    query = """
        FOR m IN membershipsHistory
            FILTER m._to == @group_id AND m.timestamp > @after
            SORT m.timestamp ASC
            RETURN m
    """
    return db.aql.execute(query, bind_vars={"after": after, "group_id": group_id})


def verify(block):
    print("SEED CONNECTED")
    
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
        cursor = db["usersInGroups"].find({"_to": seed_group["_id"]})
        members = [ug["_from"] for ug in cursor]
        for member in range(len(members)):
            if member not in seed_users:
                seed_users[members[member]] = {}
                seed_users[members[member]]["status"] = True
        memberships_history = []
        try:
            memberships_history = list(
                fetch_memberships_history(seed_group["_id"], prev_snapshot_time) # * 1000
            )  
        except:
            memberships_history = []  # membershipHisotry does not exists
        _mc = 0
        # move backward to find correct seed users in that time
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

        connections = seed_connections(seed_users_id, prev_snapshot_time)# *1000
        
        quota = seed_group.get("quota", 0)
        counter = counts.get(seed_group["_key"], 0)
        for c in connections:
            if (
                _mc < len(memberships_history)
                and "timestamp" in memberships_history[_mc]
            ):
                if (
                    memberships_history[_mc]['timestamp'] <= c['timestamp']
                ):  
                    # need to refresh seeds status
                    seed = seed_users.get(memberships_history[_mc]["_from"])
                    
                    if memberships_history[_mc]["type"] == "join":
                        seed_users[memberships_history[_mc]["_from"]]["status"] = True
                    elif memberships_history[_mc]["type"] == "leave":
                        seed_users[memberships_history[_mc]["_from"]]["status"] = False
                    _mc += 1
           
            seed = seed_users.get(c["_from"])
            # check seed user validity
            if seed["status"] == False:
                print("rejecting seed user because of leave", seed,s,seed_group["_key"])   
                continue

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
            # rank is the same, ignore
            continue
        elif(users[u].get("rank") != None):
            # needs to be updated
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


def setup_tests():
    print('Setting up tests...')
    db["variables"].insert({"_key": "PREV_SNAPSHOT_TIME", "value": 10})
    db["users"].insert_many([
        {"_key": "u1"}, {"_key": "u2"}, {"_key": "u3"}, {"_key": "u4"},
        {"_key": "u5"}, {"_key": "u6"}, {"_key": "u7"}, {"_key": "u8"},
        {"_key": "u9"}, {"_key": "u10"}
    ])
    db["groups"].insert_many([
        {"_key": "g1", "seed": True, "quota": 1000},
        {"_key": "g2", "seed": True, "quota": 2000}
    ])
    db["usersInGroups"].insert_many([
        {"_from": "users/u1", "_to": "groups/g1"},
        {"_from": "users/u4", "_to": "groups/g1"},
        {"_from": "users/u7", "_to": "groups/g2"}
    ])
    db["membershipsHistory"].insert_many([
        {"_to": "groups/g1", "_from": "users/u1", "type": "join", "timestamp": 10},
        {"_to": "groups/g1", "_from": "users/u4", "type": "join", "timestamp": 12},
        {"_to": "groups/g2", "_from": "users/u7", "type": "join", "timestamp": 15}
    ])
    db["connectionsHistory"].insert({
        "_from": "users/u1", "_to": "users/u2", "level": "just met", "timestamp": 12
    })


def setup_second_test():
    print('Setting up second test...')
    db["membershipsHistory"].insert({
        "_to": "groups/g1", "_from": "users/u4", "type": "leave", "timestamp": 17
    })
    db["connectionsHistory"].insert_many([
        {"_from": "users/u4", "_to": "users/u5", "level": "just met", "timestamp": 18},
        {"_from": "users/u7", "_to": "users/u8", "level": "just met", "timestamp": 19},
        {"_from": "users/u7", "_to": "users/u2", "level": "just met", "timestamp": 20},
        
    ])

def setup_third_test():
    print('Setting up third test...')
    db["usersInGroups"].delete_match({"_from": "users/u4", "_to": "groups/g1"})
    db["membershipsHistory"].insert({
        "_to": "groups/g2", "_from": "users/u7", "type": "leave", "timestamp": 22
    })
    db["connectionsHistory"].insert({
        "_from": "users/u7", "_to": "users/u6", "level": "just met", "timestamp": 24
    })
    db["membershipsHistory"].insert({
        "_to": "groups/g2", "_from": "users/u7", "type": "join", "timestamp": 25
    })
    db["connectionsHistory"].insert({
        "_from": "users/u7", "_to": "users/u10", "level": "just met", "timestamp": 26
    })
    

def cleanup_tests():
    try:
        db["variables"].delete("PREV_SNAPSHOT_TIME")    
    except Exception:
        pass
    
    db["users"].truncate()
    db["groups"].truncate()
    db["usersInGroups"].truncate()
    db["membershipsHistory"].truncate()
    db["verifications"].truncate()
    db["connectionsHistory"].truncate()


def test():
    cleanup_tests()
    setup_tests()
    verify(11)
    db["variables"].update({"_key": "PREV_SNAPSHOT_TIME", "value": 16})
    setup_second_test()
    verify(12)
    db["variables"].update({"_key": "PREV_SNAPSHOT_TIME", "value": 21})
    setup_third_test()
    verify(13)
    # cleanup_tests()


# test()
