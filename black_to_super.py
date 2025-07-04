from web3 import Web3
from eth_account import Account
import json
import sys
import logging
from datetime import datetime
import time

# -----------------------------
# 模块 0：日志配置
# -----------------------------
def setup_logger():
    """Configure logging with both console and file output"""
    logger = logging.getLogger('SwapLogger')
    logger.setLevel(logging.DEBUG)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.stream = open(console_handler.stream.fileno(), mode='w', encoding='utf-8', errors='replace')
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)

    # File handler
    file_handler = logging.FileHandler(f'swap_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log',encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
    file_handler.setFormatter(file_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger

# -----------------------------
# 模块 1：配置
# -----------------------------
class SwapConfig:
    """Configuration class for swap parameters"""
    RPC_URL = "https://api.avax-test.network/ext/bc/C/rpc"
    CHAIN_ID = 43113  # Fuji Testnet
    GAS_PRICE = Web3.to_wei('1', 'wei')
    GAS_MULTIPLIER = 1.2

    def __init__(self, private_key, router_address):
        self.private_key = private_key
        self.router_address = router_address
        self.router_abi = json.loads("""
        [{
          "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {
              "components": [
                {"internalType": "address", "name": "pair", "type": "address"},
                {"internalType": "address", "name": "from", "type": "address"},
                {"internalType": "address", "name": "to", "type": "address"},
                {"internalType": "bool", "name": "stable", "type": "bool"},
                {"internalType": "bool", "name": "concentrated", "type": "bool"},
                {"internalType": "address", "name": "receiver", "type": "address"}
              ],
              "internalType": "struct Route[]",
              "name": "routes",
              "type": "tuple[]"
            },
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
          ],
          "name": "swapExactTokensForTokens",
          "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
          "stateMutability": "nonpayable",
          "type": "function"
        }]
        """)

# -----------------------------
# 模块 2：Web3 初始化
# -----------------------------
class Web3Initializer:
    """Handles Web3 connection and account initialization"""
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.w3 = None
        self.account = None
        self.router = None

    def initialize_web3(self):
        """Initialize Web3 connection"""
        self.logger.info("Initializing Web3 connection...")
        self.w3 = Web3(Web3.HTTPProvider(self.config.RPC_URL))
        if not self.w3.is_connected():
            self.logger.error("Web3 connection failed")
            raise Exception("❌ Web3 connection failed")
        self.logger.info("Web3 connected successfully")
        return self.w3

    def initialize_account(self):
        """Initialize account from private key"""
        self.logger.info("Initializing account...")
        try:
            self.account = Account.from_key(self.config.private_key)
            self.logger.info(f"Account loaded: {self.account.address}")
            return self.account
        except Exception as e:
            self.logger.error(f"Account initialization failed: {str(e)}")
            raise Exception(f"❌ Account initialization failed: {str(e)}")

    def initialize_router(self):
        """Initialize router contract"""
        self.logger.info("Initializing router contract...")
        try:
            self.router = self.w3.eth.contract(
                address=self.w3.to_checksum_address(self.config.router_address),
                abi=self.config.router_abi
            )
            self.logger.info("Router contract initialized")
            return self.router
        except Exception as e:
            self.logger.error(f"Router initialization failed: {str(e)}")
            raise Exception(f"❌ Router initialization failed: {str(e)}")

# -----------------------------
# 模块 3：交易处理
# -----------------------------
class TransactionHandler:
    """Handles transaction building and sending"""
    def __init__(self, w3, router, account, logger):
        self.w3 = w3
        self.router = router
        self.account = account
        self.logger = logger

    def build_transaction(self, amount_in, amount_out_min, routes, to_address, deadline):
        """Build swap transaction"""
        self.logger.info(f"Building transaction for amount_in: {amount_in}")
        try:
            self.logger.debug("Estimating gas...")
            estimated_gas = self.router.functions.swapExactTokensForTokens(
                amount_in, amount_out_min, routes, to_address, deadline
            ).estimate_gas({'from': self.account.address})
            self.logger.debug(f"Estimated gas: {estimated_gas}")

            nonce = self.w3.eth.get_transaction_count(self.account.address)
            self.logger.debug(f"Nonce: {nonce}")

            txn = self.router.functions.swapExactTokensForTokens(
                amount_in, amount_out_min, routes, to_address, deadline
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': int(estimated_gas * SwapConfig.GAS_MULTIPLIER),
                'gasPrice': SwapConfig.GAS_PRICE,
                'chainId': SwapConfig.CHAIN_ID
            })
            self.logger.info("Transaction built successfully")
            return txn
        except Exception as e:
            self.logger.error(f"Transaction building failed: {str(e)}")
            raise Exception(f"❌ Transaction building failed: {str(e)}")

    def send_transaction(self, txn):
        """Send signed transaction"""
        self.logger.info("Sending transaction...")
        try:
            signed_txn = self.w3.eth.account.sign_transaction(txn, private_key=self.account.key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            tx_hash_hex = self.w3.to_hex(tx_hash)
            self.logger.info(f"Transaction sent successfully. Hash: {tx_hash_hex}")
            return tx_hash_hex
        except Exception as e:
            self.logger.error(f"Transaction sending failed: {str(e)}")
            raise Exception(f"❌ Transaction sending failed: {str(e)}")

# -----------------------------
# 模块 4：主逻辑
# -----------------------------
class TokenSwap:
    """Main class for executing token swap"""
    def __init__(self, private_key, router_address):
        self.logger = setup_logger()
        self.config = SwapConfig(private_key, router_address)
        self.web3_initializer = Web3Initializer(self.config, self.logger)
        self.w3 = None
        self.account = None
        self.router = None

    def initialize(self):
        """Initialize all components"""
        self.logger.info("Starting swap initialization...")
        try:
            self.w3 = self.web3_initializer.initialize_web3()
            self.account = self.web3_initializer.initialize_account()
            self.router = self.web3_initializer.initialize_router()
            self.logger.info(f"Wallet address: {self.account.address}")
        except Exception as e:
            self.logger.error(f"Initialization failed: {str(e)}")
            raise

    def execute_swap(self, amount_in, amount_out_min, routes, to_address, deadline):
        """Execute the token swap"""
        self.logger.info("Executing token swap...")
        try:
            tx_handler = TransactionHandler(self.w3, self.router, self.account, self.logger)
            txn = tx_handler.build_transaction(amount_in, amount_out_min, routes, to_address, deadline)
            
            self.logger.info("Waiting for transaction confirmation...")
            tx_hash = tx_handler.send_transaction(txn)
            
            # Wait for transaction receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt.status == 1:
                self.logger.info(f"✅ Swap successful! Transaction hash: {tx_hash}")
                return tx_hash
            else:
                self.logger.error("Transaction failed (status=0)")
                raise Exception("❌ Transaction failed (status=0)")
        except Exception as e:
            self.logger.error(f"Swap execution failed: {str(e)}")
            raise

# -----------------------------
# 模块 5：执行入口
# -----------------------------
def main():
    # Configuration
    PRIVATE_KEY = "xxxxxxxxx"  # Replace with actual private key
    ROUTER_ADDRESS = "0x1B6814F3227a246F62bC47b148b3d288Dbc85715"  # Replace with actual router address

    # Swap parameters
    amount_in = int("8ac7230489e80000", 16)  # 10^18
    amount_out_min = 0
    deadline = int(time.time()) + 1800  # 30 minutes from now
    
    routes = [{
        "pair": Web3.to_checksum_address("0xfa01e6325ad1012b6f855d09a862ea3dba7ef5da"),
        "from": Web3.to_checksum_address("0xa981371a120b0e1bbdcd0abab1ed509c1084fe5f"),
        "to": Web3.to_checksum_address("0x2d65b197f04109724dfac2ec74775190eac7af7d"),
        "stable": False,
        "concentrated": False,
        "receiver": Web3.to_checksum_address("xxxxxxx")
    }]
    
    to_address = Web3.to_checksum_address("xxxxx")

    # Execute swap
    swap = TokenSwap(PRIVATE_KEY, ROUTER_ADDRESS)
    try:
        swap.initialize()
        tx_hash = swap.execute_swap(amount_in, amount_out_min, routes, to_address, deadline)
        print(f"✅ Swap completed successfully! Transaction hash: {tx_hash}")
    except Exception as e:
        print(f"❌ Swap failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
