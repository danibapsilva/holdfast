// SPDX-License-Identifier: LicenseRef-PolyForm-Noncommercial-1.0.0
pragma solidity ^0.8.24;

/// @title Holdfast Test USD (tUSD)
/// @notice A TEST TOKEN with no value, for the Holdfast escrow demo on the Sepolia test network.
///         It refuses to be deployed anywhere but Sepolia or a local development chain.
///         Anyone can take a little from the faucet once a day; the demo's minter account can mint
///         small amounts so the demo bot can play the paying side of a trade.
contract TestUSD {
    string public constant name = "Holdfast Test USD";
    string public constant symbol = "tUSD";
    uint8 public constant decimals = 6;

    uint256 public constant SEPOLIA_CHAIN_ID = 11155111;
    uint256 public constant LOCAL_CHAIN_ID = 31337;
    uint256 public constant MINT_CAP = 1_000 * 10 ** 6; // most the minter can mint in one call
    uint256 public constant FAUCET_AMOUNT = 100 * 10 ** 6;
    uint256 public constant FAUCET_COOLDOWN = 1 days;

    address public immutable minter;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    mapping(address => uint256) public lastFaucet;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    error TestnetsOnly(uint256 chainId);
    error NotMinter();
    error OverMintCap();
    error FaucetCooldown(uint256 availableAt);
    error InsufficientBalance();
    error InsufficientAllowance();
    error ZeroAddress();

    constructor(address minter_) {
        if (block.chainid != SEPOLIA_CHAIN_ID && block.chainid != LOCAL_CHAIN_ID) revert TestnetsOnly(block.chainid);
        if (minter_ == address(0)) revert ZeroAddress();
        minter = minter_;
    }

    function transfer(address to, uint256 value) external returns (bool) {
        _transfer(msg.sender, to, value);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) {
            if (allowed < value) revert InsufficientAllowance();
            allowance[from][msg.sender] = allowed - value;
        }
        _transfer(from, to, value);
        return true;
    }

    /// @notice The demo's minter funds the demo bot's side of a trade, a capped amount at a time.
    function mint(address to, uint256 value) external {
        if (msg.sender != minter) revert NotMinter();
        if (value > MINT_CAP) revert OverMintCap();
        _mint(to, value);
    }

    /// @notice 100 tUSD to whoever asks, once a day per address.
    function faucet() external {
        uint256 next = lastFaucet[msg.sender] + FAUCET_COOLDOWN;
        if (lastFaucet[msg.sender] != 0 && block.timestamp < next) revert FaucetCooldown(next);
        lastFaucet[msg.sender] = block.timestamp;
        _mint(msg.sender, FAUCET_AMOUNT);
    }

    function _transfer(address from, address to, uint256 value) private {
        if (to == address(0)) revert ZeroAddress();
        uint256 balance = balanceOf[from];
        if (balance < value) revert InsufficientBalance();
        unchecked {
            balanceOf[from] = balance - value;
        }
        balanceOf[to] += value;
        emit Transfer(from, to, value);
    }

    function _mint(address to, uint256 value) private {
        if (to == address(0)) revert ZeroAddress();
        totalSupply += value;
        balanceOf[to] += value;
        emit Transfer(address(0), to, value);
    }
}
