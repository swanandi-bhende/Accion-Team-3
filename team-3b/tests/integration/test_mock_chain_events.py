import os
import json
import asyncio
import pytest
from web3 import Web3, AsyncWeb3

@pytest.fixture
def w3():
    # Use HTTP provider for simple local testing against hardhat node
    return Web3(Web3.HTTPProvider('http://127.0.0.1:8545'))

def load_abi(contract_name):
    path = f"mocks/contracts/artifacts/contracts/{contract_name}.sol/{contract_name}.json"
    with open(path) as f:
        return json.load(f)["abi"]

def load_bytecode(contract_name):
    path = f"mocks/contracts/artifacts/contracts/{contract_name}.sol/{contract_name}.json"
    with open(path) as f:
        return json.load(f)["bytecode"]

def test_mock_chain_events_and_submit(w3):
    # Ensure node is up
    assert w3.is_connected()

    account = w3.eth.accounts[0]
    w3.eth.default_account = account

    # 1. Deploy TaskManager
    tm_abi = load_abi("TaskManager")
    tm_bytecode = load_bytecode("TaskManager")
    TaskManager = w3.eth.contract(abi=tm_abi, bytecode=tm_bytecode)
    tx_hash = TaskManager.constructor().transact()
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    tm_address = tx_receipt.contractAddress
    task_manager = w3.eth.contract(address=tm_address, abi=tm_abi)

    # 2. Deploy Escrow
    escrow_abi = load_abi("Escrow")
    escrow_bytecode = load_bytecode("Escrow")
    Escrow = w3.eth.contract(abi=escrow_abi, bytecode=escrow_bytecode)
    tx_hash = Escrow.constructor().transact()
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    escrow_address = tx_receipt.contractAddress
    escrow = w3.eth.contract(address=escrow_address, abi=escrow_abi)

    # 3. Trigger TaskCreated
    desc_hash = w3.keccak(text="mock_description")
    deadline = 9999999999
    bounty = w3.to_wei(1, 'ether')
    tx_hash = task_manager.functions.createTask(desc_hash, deadline, bounty).transact()
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    # Parse event
    events = task_manager.events.TaskCreated().process_receipt(receipt)
    assert len(events) == 1
    task_id = events[0]['args']['taskId']
    assert events[0]['args']['descriptionHash'] == desc_hash

    # 4. Trigger TaskFunded
    tx_hash = escrow.functions.fundTask(task_id).transact({'value': bounty})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    # Parse event
    events = escrow.events.TaskFunded().process_receipt(receipt)
    assert len(events) == 1
    assert events[0]['args']['taskId'] == task_id
    assert events[0]['args']['fundedAmount'] == bounty

    # 5. Round trip submitProofs
    proof_hash = w3.keccak(text="proof")
    model_cid = "Qm123"
    result_hash = w3.keccak(text="result")
    
    tx_hash = task_manager.functions.submitProofs(
        [task_id],
        [proof_hash],
        [model_cid],
        [result_hash]
    ).transact()
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    assert receipt.status == 1 # Success
