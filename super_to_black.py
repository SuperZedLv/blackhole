from web3 import Web3
from eth_account import Account
import json
import logging
from datetime import datetime, timedelta

# -----------------------------
# 模块 0：日志配置
# -----------------------------
def setup_logger():
    """配置日志，支持 UTF-8 编码"""
    logger = logging.getLogger('SwapLogger')
    logger.setLevel(logging.DEBUG)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.stream = open(console_handler.stream.fileno(), mode='w', encoding='utf-8', errors='replace')
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    file_handler = logging.FileHandler(f'swap_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'))
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger

# -----------------------------
# 模块 1：配置
# -----------------------------
class SwapConfig:
    RPC_URL = "https://api.avax-test.network/ext/bc/C/rpc"
    CHAIN_ID = 43113
    GAS_PRICE = Web3.to_wei('1', 'wei')
    GAS_MULTIPLIER = 1.2
    ROUTER_ABI = json.loads('''
    [{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"amountOutMin","type":"uint256"},{"components":[{"internalType":"address","name":"pair","type":"address"},{"internalType":"address","name":"from","type":"address"},{"internalType":"address","name":"to","type":"address"},{"internalType":"bool","name":"stable","type":"bool"},{"internalType":"bool","name":"concentrated","type":"bool"},{"internalType":"address","name":"receiver","type":"address"}],"internalType":"struct Route[]","name":"routes","type":"tuple[]"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"swapExactTokensForTokens","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"nonpayable","type":"function"}]
    ''')
    ERC20_ABI = json.loads('''
    [
        {"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
        {"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"remaining","type":"uint256"}],"type":"function"},
        {"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"success","type":"bool"}],"type":"function"}
    ]
    ''')

    def __init__(self, private_key, router_address):
        self.private_key = private_key
        self.router_address = router_address

# -----------------------------
# 模块 2：核心交换逻辑
# -----------------------------
class TokenSwap:
    def __init__(self, private_key, router_address):
        self.logger = setup_logger()
        self.config = SwapConfig(private_key, router_address)
        self.w3 = None
        self.account = None
        self.router = None

    def initialize(self):
        """初始化 Web3、账户和合约"""
        self.logger.info("初始化 Web3 连接...")
        self.w3 = Web3(Web3.HTTPProvider(self.config.RPC_URL))
        if not self.w3.is_connected():
            self.logger.error("Web3 连接失败")
            raise Exception("[错误] Web3 连接失败")
        
        self.logger.info("初始化账户...")
        self.account = Account.from_key(self.config.private_key)
        self.logger.info(f"账户地址: {self.account.address}")
        
        self.logger.info("初始化路由合约...")
        self.router = self.w3.eth.contract(address=self.w3.to_checksum_address(self.config.router_address), abi=self.config.ROUTER_ABI)
        self.logger.info("初始化完成")

    def check_balance_and_approval(self, token_address, amount_in):
        """检查代币余额和授权"""
        token = self.w3.eth.contract(address=self.w3.to_checksum_address(token_address), abi=self.config.ERC20_ABI)
        
        # 检查余额
        balance = token.functions.balanceOf(self.account.address).call()
        self.logger.info(f"代币余额: {balance} (所需: {amount_in})")
        if balance < amount_in:
            self.logger.error("余额不足")
            raise Exception(f"[错误] 余额不足: 现有 {balance}, 所需 {amount_in}")
        
        # 检查授权
        allowance = token.functions.allowance(self.account.address, self.config.router_address).call()
        self.logger.info(f"当前授权金额: {allowance} (所需: {amount_in})")
        if allowance < amount_in:
            self.logger.info("执行代币授权...")
            approve_txn = token.functions.approve(
                self.config.router_address,
                amount_in
            ).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
                'gas': 100000,
                'gasPrice': self.config.GAS_PRICE,
                'chainId': self.config.CHAIN_ID
            })
            signed_approve_txn = self.w3.eth.account.sign_transaction(approve_txn, self.account.key)
            approve_tx_hash = self.w3.eth.send_raw_transaction(signed_approve_txn.raw_transaction)
            approve_tx_hash_hex = self.w3.to_hex(approve_tx_hash)
            self.logger.info(f"授权交易已发送: {approve_tx_hash_hex}")
            receipt = self.w3.eth.wait_for_transaction_receipt(approve_tx_hash, timeout=120)
            if receipt.status != 1:
                self.logger.error("授权交易失败")
                raise Exception("[错误] 授权交易失败")

    def execute_swap(self, amount_in, amount_out_min, routes, to_address, deadline):
        """执行代币交换"""
        # 检查余额和授权
        token_address = routes[0]["from"]  # 假设第一个路由的 from 是输入代币
        self.check_balance_and_approval(token_address, amount_in)
        
        self.logger.info(f"构建交易，输入金额: {amount_in}")
        try:
            # 估算 Gas
            estimated_gas = self.router.functions.swapExactTokensForTokens(
                amount_in, amount_out_min, routes, to_address, deadline
            ).estimate_gas({'from': self.account.address})
            self.logger.debug(f"估算 Gas: {estimated_gas}")

            # 构建交易
            txn = self.router.functions.swapExactTokensForTokens(
                amount_in, amount_out_min, routes, to_address, deadline
            ).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
                'gas': int(estimated_gas * self.config.GAS_MULTIPLIER),
                'gasPrice': self.config.GAS_PRICE,
                'chainId': self.config.CHAIN_ID
            })
            self.logger.info("交易构建完成")

            # 签名并发送交易
            self.logger.info("发送交易...")
            signed_txn = self.w3.eth.account.sign_transaction(txn, self.account.key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            tx_hash_hex = self.w3.to_hex(tx_hash)
            self.logger.info(f"交易已发送，哈希: {tx_hash_hex}")

            # 等待交易确认
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt.status == 1:
                self.logger.info(f"[成功] 交易确认，哈希: {tx_hash_hex}")
                return tx_hash_hex
            else:
                self.logger.error("交易失败 (status=0)")
                raise Exception("[错误] 交易失败 (status=0)")
        except Exception as e:
            self.logger.error(f"交换失败: {str(e)}")
            raise

# -----------------------------
# 模块 3：执行入口
# -----------------------------
def main():
    # 配置
    PRIVATE_KEY = "xxxx"  # 请替换为实际私钥
    ROUTER_ADDRESS = "0x1B6814F3227a246F62bC47b148b3d288Dbc85715"  # 请替换为实际路由地址

    # 交易参数
    amount_in = 0x0de0b6b3a7640000  # 1e18 (1 token)
    amount_out_min = 0  # ~20.5 tokens
    to_address = Web3.to_checksum_address("xxxx")
    deadline = int((datetime.now() + timedelta(minutes=30)).timestamp())  # 动态截止时间：当前时间 + 30 分钟
    routes = [{
        "pair": Web3.to_checksum_address("0xfa01e6325ad1012b6f855d09a862ea3dba7ef5da"),
        "from": Web3.to_checksum_address("0x2d65b197f04109724dfac2ec74775190eac7af7d"),
        "to": Web3.to_checksum_address("0xa981371a120b0e1bbdcd0abab1ed509c1084fe5f"),
        "stable": False,
        "concentrated": False,
        "receiver": Web3.to_checksum_address("xxxx")
    }]

    # 执行交换
    swap = TokenSwap(PRIVATE_KEY, ROUTER_ADDRESS)
    try:
        swap.initialize()
        tx_hash = swap.execute_swap(amount_in, amount_out_min, routes, to_address, deadline)
        print(f"[成功] 代币交换完成！交易哈希: {tx_hash}")
    except Exception as e:
        print(f"[错误] 代币交换失败: {str(e)}")
        exit(1)

if __name__ == "__main__":
    main()
