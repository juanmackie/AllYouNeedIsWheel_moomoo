import { formatCurrency, formatPercent } from '../utils/formatters.js';
import { state } from './options-table-state.js';

function calculatePremium(bid, ask, last) {
    const bidNum = parseFloat(bid || 0);
    const askNum = parseFloat(ask || 0);
    const lastNum = parseFloat(last || 0);

    if (bidNum > 0 && askNum > 0) {
        return (bidNum + askNum) / 2;
    }
    if (bidNum > 0) {
        return bidNum;
    }
    if (askNum > 0) {
        return askNum;
    }
    if (lastNum > 0) {
        return lastNum;
    }
    return 0.05;
}

function getPremiumPerContract(option) {
    if (!option) return null;

    const premiumPerContract = Number(option.premium_per_contract);
    if (premiumPerContract > 0) {
        return premiumPerContract;
    }

    const midPrice = Number(option.mid_price);
    if (midPrice > 0) {
        return midPrice * 100;
    }

    const bid = parseFloat(option.bid || 0);
    const ask = parseFloat(option.ask || 0);
    if (bid > 0 && ask > 0) {
        return ((bid + ask) / 2) * 100;
    }

    return null;
}

function calculateEarningsSummary() {
    const summary = {
        totalWeeklyCallPremium: 0,
        totalWeeklyPutPremium: 0,
        totalWeeklyPremium: 0,
        portfolioValue: 0,
        projectedAnnualEarnings: 0,
        projectedAnnualReturn: 0,
        weeklyReturn: 0,
        totalPutExerciseCost: 0,
        cashBalance: state.portfolioSummary ? state.portfolioSummary.cash_balance || 0 : 0
    };

    Object.values(state.tickersData).forEach(tickerData => {
        if (!tickerData || !tickerData.data || !tickerData.data.data) {
            return;
        }
        Object.values(tickerData.data.data).forEach(optionData => {
            const sharesOwned = optionData.position || 0;
            const ticker = optionData.symbol || Object.keys(tickerData.data.data)[0];
            const isWatchlistTicker = state.watchlistTickers?.has(ticker);

            if (sharesOwned < 100 && !isWatchlistTicker) {
                return;
            }

            const stockPrice = optionData.stock_price || 0;
            summary.portfolioValue += sharesOwned * stockPrice;

            const maxCallContracts = Math.floor(sharesOwned / 100);

            if (sharesOwned >= 100 && optionData.calls && optionData.calls.length > 0) {
                const callOption = optionData.calls[0];
                if (callOption) {
                    const callPremiumPerContract = getPremiumPerContract(callOption);
                    if (callPremiumPerContract != null) {
                        const totalCallPremium = callPremiumPerContract * maxCallContracts;
                        summary.totalWeeklyCallPremium += totalCallPremium;
                    }
                }
            }

            if ((sharesOwned >= 100 || isCustomTicker || isWatchlistTicker) && optionData.puts && optionData.puts.length > 0) {
                const putOption = optionData.puts[0];
                if (putOption) {
                    const putPremiumPerContract = getPremiumPerContract(putOption);
                    const customPutQuantity = tickerData.putQuantity ||
                                             (sharesOwned >= 100 ? Math.floor(sharesOwned / 100) : 1);
                    if (putPremiumPerContract != null) {
                        const totalPutPremium = putPremiumPerContract * customPutQuantity;
                        summary.totalWeeklyPutPremium += totalPutPremium;
                    }
                    const putExerciseCost = putOption.strike * customPutQuantity * 100;
                    summary.totalPutExerciseCost += putExerciseCost;
                }
            }
        });
    });

    summary.totalWeeklyPremium = summary.totalWeeklyCallPremium + summary.totalWeeklyPutPremium;

    let totalPortfolioValue = 0;

    if (state.portfolioSummary) {
        totalPortfolioValue = state.portfolioSummary.account_value || 0;
        if (totalPortfolioValue === 0) {
            const stockValue = state.portfolioSummary.stock_value || 0;
            const cashBalance = state.portfolioSummary.cash_balance || 0;
            totalPortfolioValue = stockValue + cashBalance;
            summary.portfolioValue = stockValue;
            summary.cashBalance = cashBalance;
        } else {
            summary.portfolioValue = state.portfolioSummary.stock_value || 0;
            summary.cashBalance = state.portfolioSummary.cash_balance || 0;
        }
    }

    if (totalPortfolioValue === 0 && window.portfolioData) {
        summary.portfolioValue = window.portfolioData.stockValue || 0;
        summary.cashBalance = window.portfolioData.cashBalance || 0;
        totalPortfolioValue = summary.portfolioValue + summary.cashBalance;
    }

    if (totalPortfolioValue > 0) {
        summary.weeklyReturn = (summary.totalWeeklyPremium / totalPortfolioValue) * 100;
        summary.projectedAnnualEarnings = summary.totalWeeklyPremium * 52;
        summary.projectedAnnualReturn = (summary.projectedAnnualEarnings / totalPortfolioValue) * 100;
    } else {
        summary.projectedAnnualEarnings = summary.totalWeeklyPremium * 52;
    }

    return summary;
}

function updateEarningsSummary() {
    const earningsSummary = calculateEarningsSummary();

    const summarySection = document.querySelector('.card.shadow-sm.mt-4');
    if (!summarySection) return;

    const weeklyCallsPremiumCell = summarySection.querySelector('td:nth-child(2)');
    const weeklyPutsPremiumCell = summarySection.querySelector('td:nth-child(3)');
    const weeklyTotalPremiumCell = summarySection.querySelector('td:nth-child(4)');
    const weeklyReturnCell = summarySection.querySelector('td:nth-child(6)');
    const annualReturnCell = summarySection.querySelector('td:nth-child(7)');

    const stockValueCell = summarySection.querySelector('tr:nth-child(2) td:nth-child(2)');
    const cashBalanceCell = summarySection.querySelector('tr:nth-child(2) td:nth-child(3)');
    const cspRequirementCell = summarySection.querySelector('tr:nth-child(2) td:nth-child(4)');
    const annualIncomeCell = summarySection.querySelector('tr:nth-child(2) td:nth-child(6)');

    if (weeklyCallsPremiumCell) weeklyCallsPremiumCell.textContent = `Calls: ${formatCurrency(earningsSummary.totalWeeklyCallPremium)}`;
    if (weeklyPutsPremiumCell) weeklyPutsPremiumCell.textContent = `Puts: ${formatCurrency(earningsSummary.totalWeeklyPutPremium)}`;
    if (weeklyTotalPremiumCell) weeklyTotalPremiumCell.textContent = `Total: ${formatCurrency(earningsSummary.totalWeeklyPremium)}`;
    if (weeklyReturnCell) weeklyReturnCell.textContent = formatPercent(earningsSummary.weeklyReturn);
    if (annualReturnCell) annualReturnCell.textContent = `Annual: ${formatPercent(earningsSummary.projectedAnnualReturn)}`;

    if (stockValueCell) stockValueCell.textContent = `Stock: ${formatCurrency(earningsSummary.portfolioValue)}`;
    if (cashBalanceCell) cashBalanceCell.textContent = `Cash: ${formatCurrency(earningsSummary.cashBalance)}`;
    if (cspRequirementCell) cspRequirementCell.textContent = `CSP Requirement: ${formatCurrency(earningsSummary.totalPutExerciseCost)}`;
    if (annualIncomeCell) annualIncomeCell.textContent = formatCurrency(earningsSummary.projectedAnnualEarnings);
}

export {
    calculatePremium,
    getPremiumPerContract,
    calculateEarningsSummary,
    updateEarningsSummary,
};
