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

function calculateOTMPercentage(strikePrice, currentPrice) {
    if (!strikePrice || !currentPrice) return 0;
    const diff = strikePrice - currentPrice;
    return (diff / currentPrice) * 100;
}

function calculateRecommendedPutQuantity(stockPrice, putStrike, ticker) {
    const defaultRecommendation = {
        quantity: 1,
        explanation: "Default recommendation"
    };
    if (!state.portfolioSummary || !stockPrice || !putStrike) {
        return defaultRecommendation;
    }
    try {
        const cashBalance = state.portfolioSummary.cash_balance || 0;
        const totalStocks = Object.keys(state.tickersData).length || 1;
        const maxAllocationPerStock = (2.0 * cashBalance) / totalStocks;
        const potentialContracts = Math.floor(maxAllocationPerStock / (putStrike * 100));
        const maxContracts = Math.min(potentialContracts, 10);
        const recommendedQuantity = Math.max(1, maxContracts);
        return {
            quantity: recommendedQuantity,
            explanation: `Based on cash: ${formatCurrency(cashBalance)}, diversification across ${totalStocks} stocks`
        };
    } catch (error) {
        console.error("Error calculating recommended put quantity:", error);
        return defaultRecommendation;
    }
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
            const isCustomTicker = state.customTickers.has(ticker);

            if (sharesOwned < 100 && !isCustomTicker) {
                return;
            }

            const stockPrice = optionData.stock_price || 0;
            summary.portfolioValue += sharesOwned * stockPrice;

            const maxCallContracts = Math.floor(sharesOwned / 100);

            if (sharesOwned >= 100 && optionData.calls && optionData.calls.length > 0) {
                const callOption = optionData.calls[0];
                if (callOption) {
                    const callPremiumPerContract = calculatePremium(callOption.bid, callOption.ask, callOption.last) * 100;
                    const totalCallPremium = callPremiumPerContract * maxCallContracts;
                    summary.totalWeeklyCallPremium += totalCallPremium;
                }
            }

            if ((sharesOwned >= 100 || isCustomTicker) && optionData.puts && optionData.puts.length > 0) {
                const putOption = optionData.puts[0];
                if (putOption) {
                    const putPremiumPerContract = calculatePremium(putOption.bid, putOption.ask, putOption.last) * 100;
                    const customPutQuantity = tickerData.putQuantity ||
                                             (sharesOwned >= 100 ? Math.floor(sharesOwned / 100) : 1);
                    const totalPutPremium = putPremiumPerContract * customPutQuantity;
                    summary.totalWeeklyPutPremium += totalPutPremium;
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
    calculateOTMPercentage,
    calculateRecommendedPutQuantity,
    calculateEarningsSummary,
    updateEarningsSummary,
};
