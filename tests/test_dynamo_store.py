import boto3
from moto import mock_aws

from agent_host.models import ConversationTurn
from agent_host.store.dynamo_store import DynamoStore


@mock_aws
def test_dynamo_roundtrip_and_namespacing():
    res = boto3.resource("dynamodb", region_name="us-east-1")
    res.create_table(
        TableName="t",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    store = DynamoStore("t", resource=res)
    brief = store.namespaced("brief")
    chat = store.namespaced("chat")

    chat.save_memory("42", [ConversationTurn(role="user", content="hi")])
    assert [t.content for t in chat.load_memory("42")] == ["hi"]
    assert brief.load_memory("42") == []          # namespace isolation

    brief.set_prefs("42", {"lang": "zh"})
    assert brief.get_prefs("42") == {"lang": "zh"}
    assert brief.seen("h1") is False
    brief.mark_seen(["h1"])
    assert brief.seen("h1") is True
