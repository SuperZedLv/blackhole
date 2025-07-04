from web3 import Web3
from eth_account import Account
import json
import logging
import os
from dotenv import load_dotenv
from datetime import datetime

# 加载 .env 文件
load_dotenv()

# -----------------------------
# 模块 0：日志配置
# -----------------------------
def setup_logger():
    """配置日志，支持 UTF-8 编码"""
    logger = logging.getLogger('StakeLogger')
    logger.setLevel(logging.DEBUG)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.stream = open(console_handler.stream.fileno(), mode='w', encoding='utf-8', errors='replace')
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    file_handler = logging.FileHandler(f'stake_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'))
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger

# -----------------------------
# 模块 1：配置
# -----------------------------
class StakeConfig:
    RPC_URL = "https://api.avax-test.network/ext/bc/C/rpc"
    CHAIN_ID = 43113
    GAS_PRICE = Web3.to_wei('2', 'wei')  # 提高 Gas 价格
    GAS_MULTIPLIER = 1.5  # 提高 Gas 倍数
    STAKING_ABI = json.loads('''
    [
        {"inputs":[{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"deposit","outputs":[],"stateMutability":"nonpayable","type":"function"},
        {"inputs":[],"name":"totalStaked","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
        {"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
        {"inputs":[],"name":"stakingToken","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},
        {"inputs":[],"name":"maxStakePerUser","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
        {"inputs":[],"name":"paused","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"}
    ]
    ''')
    ERC20_ABI = json.loads('''
    [
        {"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
        {"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"remaining","type":"uint256"}],"type":"function"},
        {"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"success","type":"bool"}],"type":"function"},
        {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}
    ]
    ''')

    def __init__(self):
        self.private_key = os.getenv('PRIVATE_KEY')
        self.staking_contract_address = Web3.to_checksum_address(os.getenv('STAKING_CONTRACT_ADDRESS', '0x91Ad32cC40E14c3E2d843aAAA51635a11a022465'))
        self.token_address = Web3.to_checksum_address(os.getenv('TOKEN_ADDRESS', '0x2d65b197f04109724dfac2ec74775190eac7af7d'))
        if not all([self.private_key, self.staking_contract_address, self.token_address]):
            raise ValueError("[错误] 配置文件中缺少 PRIVATE_KEY、STAKING_CONTRACT_ADDRESS 或 TOKEN_ADDRESS")

# -----------------------------
# 模块 2：核心质押逻辑
# -----------------------------
class TokenStaker:
    def __init__(self):
        self.logger = setup_logger()
        self.config = StakeConfig()
        self.w3 = None
        self.account = None
        self.staking_contract = None
        self.token = None
        self.decimals = None
        self.MIN_STAKE_AMOUNT = 10**15  # 0.001 tokens，防止除以0或panic

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
        
        self.logger.info("初始化质押合约...")
        self.staking_contract = self.w3.eth.contract(address=self.config.staking_contract_address, abi=self.config.STAKING_ABI)
        self.logger.info("初始化代币合约...")
        self.token = self.w3.eth.contract(address=self.config.token_address, abi=self.config.ERC20_ABI)
        
        # 查询代币精度
        try:
            self.decimals = self.token.functions.decimals().call()
            self.logger.info(f"代币精度: {self.decimals}")
        except Exception as e:
            self.logger.warning(f"无法查询代币精度，假设为 18: {str(e)}")
            self.decimals = 18
        
        # 合约功能检查
        try:
            staking_token = self.staking_contract.functions.stakingToken().call()
            self.logger.info(f"质押合约接受的代币: {staking_token}")
            if staking_token.lower() != self.config.token_address.lower():
                raise Exception(f"[错误] 代币地址不匹配，合约要求: {staking_token}, 提供: {self.config.token_address}")
        except Exception as e:
            self.logger.warning(f"无法查询 stakingToken: {str(e)}")
        
        try:
            total_staked = self.staking_contract.functions.totalStaked().call()
            self.logger.info(f"当前总质押量: {total_staked} ({total_staked / 10**self.decimals} tokens)")
        except:
            self.logger.warning("无法查询 totalStaked，可能合约无此函数")
        
        try:
            user_staked = self.staking_contract.functions.balanceOf(self.account.address).call()
            self.logger.info(f"用户当前质押量: {user_staked} ({user_staked / 10**self.decimals} tokens)")
        except:
            self.logger.warning("无法查询 balanceOf，可能合约无此函数")
        
        try:
            max_stake = self.staking_contract.functions.maxStakePerUser().call()
            self.logger.info(f"最大单用户质押量: {max_stake} ({max_stake / 10**self.decimals} tokens)")
        except:
            self.logger.warning("无法查询 maxStakePerUser，可能合约无此函数")
        
        try:
            paused = self.staking_contract.functions.paused().call()
            self.logger.info(f"合约暂停状态: {paused}")
        except:
            self.logger.warning("无法查询 paused，可能合约无此函数")
        
        block = self.w3.eth.get_block('latest')
        self.logger.info(f"当前块高: {block['number']}, 时间戳: {block['timestamp']}")
        self.logger.info("初始化完成")

    def check_balance_and_approval(self, amount):
        """检查代币余额和授权"""
        if amount < self.MIN_STAKE_AMOUNT:
            self.logger.error(f"质押金额过小（{amount} wei），容易引发合约 panic")
            raise Exception("[错误] 质押金额过小，请增加质押数额 (建议 > 0.001 tokens)")

        try:
            max_stake = self.staking_contract.functions.maxStakePerUser().call()
            user_staked = self.staking_contract.functions.balanceOf(self.account.address).call()
            total_allowed = max_stake - user_staked
            if amount > total_allowed:
                self.logger.error(f"质押金额超出剩余限额: {amount} wei")
                raise Exception("[错误] 质押金额超过限额")
        except:
            self.logger.warning("无法验证最大质押限制，跳过此检查")

        balance = self.token.functions.balanceOf(self.account.address).call()
        self.logger.info(f"代币余额: {balance}，所需: {amount}")
        if balance < amount:
            raise Exception("[错误] 余额不足")

        allowance = self.token.functions.allowance(self.account.address, self.config.staking_contract_address).call()
        if allowance < amount:
            self.logger.info("执行授权...")
            approve_txn = self.token.functions.approve(
                self.config.staking_contract_address, amount
            ).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
                'gas': 100000,
                'gasPrice': self.config.GAS_PRICE,
                'chainId': self.config.CHAIN_ID
            })
            signed_approve = self.w3.eth.account.sign_transaction(approve_txn, self.config.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_approve.raw_transaction)
            self.logger.info(f"授权交易发送: {self.w3.to_hex(tx_hash)}")
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            if receipt.status != 1:
                raise Exception("[错误] 授权失败")
            self.logger.info("授权完成")

    def deposit(self, amount):
        """执行质押操作"""

        self.check_balance_and_approval(amount)

        # dry-run 测试
        try:
            self.logger.debug("执行合约 dry-run...")
            self.staking_contract.functions.deposit(amount).call({'from': self.account.address})
        except Exception as e:
            self.logger.error(f"合约 dry-run 失败，可能因状态异常或金额太小：{str(e)}")
            raise Exception("[错误] 合约 call 检测失败，请检查金额或合约状态")

        try:
            estimated_gas = self.staking_contract.functions.deposit(amount).estimate_gas({'from': self.account.address})
            txn = self.staking_contract.functions.deposit(amount).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
                'gas': int(estimated_gas * self.config.GAS_MULTIPLIER),
                'gasPrice': self.config.GAS_PRICE,
                'chainId': self.config.CHAIN_ID
            })
            signed = self.w3.eth.account.sign_transaction(txn, self.config.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            self.logger.info(f"交易发送成功，Hash: {self.w3.to_hex(tx_hash)}")

            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            if receipt.status != 1:
                raise Exception("[错误] 质押交易失败（合约回滚）")
            self.logger.info("[成功] 质押交易确认")
            return self.w3.to_hex(tx_hash)
        except Exception as e:
            self.logger.error(f"质押失败: {str(e)}")
            raise

# -----------------------------  
# 模块 3：执行入口（建议金额提升）  
# -----------------------------  

def main():
    # 建议质押金额 >= 5 tokens，防止触发 underflow
    amount_tokens = 0.99
    amount = int(amount_tokens * 10**18)

    staker = TokenStaker()
    try:
        staker.initialize()
        tx_hash = staker.deposit(amount)
        print(f"[成功] 质押完成！交易哈希: {tx_hash}")
    except Exception as e:
        print(f"[错误] 质押失败: {str(e)}")
        exit(1)

if __name__ == "__main__":
    main()
