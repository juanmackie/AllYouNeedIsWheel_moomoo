function formatPercentage(value, includeColorClass = true) {
    if (value === null || value === undefined) return '0.00%';
    const percentStr = `${Math.abs(value).toFixed(2)}%`;
    if (!includeColorClass) return percentStr;
    if (value < 5) {
        return `<span class="text-danger fw-bold">${percentStr}</span>`;
    } else if (value < 10) {
        return `<span class="text-danger">${percentStr}</span>`;
    } else {
        return `<span>${percentStr}</span>`;
    }
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    try {
        const date = new Date(dateStr);
        if (isNaN(date.getTime())) return dateStr;
        return date.toLocaleString();
    } catch (e) {
        return dateStr;
    }
}

function getBadgeColor(status) {
    if (!status) return 'secondary';
    status = status.toLowerCase();
    if (status === 'executed' || status === 'filled') {
        return 'success';
    } else if (status === 'cancelled' || status === 'canceled' || status === 'rejected') {
        return 'danger';
    } else if (status === 'processing') {
        return 'warning';
    } else if (status === 'ready') {
        return 'info';
    } else {
        return 'secondary';
    }
}

function calculateMidPrice(bid, ask) {
    const bidNum = bid || 0;
    const askNum = ask || 0;
    if (bidNum > 0 && askNum > 0) {
        return (bidNum + askNum) / 2;
    }
    return bidNum > 0 ? bidNum : (askNum > 0 ? askNum : 0);
}

function calculateTargetStrike(stockPrice, otmPercentage, optionType) {
    if (optionType === 'CALL' || optionType === 'C' || optionType === 'Call') {
        return stockPrice * (1 + otmPercentage / 100);
    }
    return stockPrice * (1 - otmPercentage / 100);
}

function roundStrikeToNearestHalf(strike) {
    return Math.round(strike * 2) / 2;
}

function parseExpirationDate(expiration) {
    if (!expiration) return null;
    try {
        if (expiration.includes('-')) {
            return new Date(expiration);
        } else if (expiration.includes('/')) {
            const parts = expiration.split('/');
            return new Date(parts[2], parts[0] - 1, parts[1]);
        } else if (/^\d{8}$/.test(expiration)) {
            const year = parseInt(expiration.substring(0, 4));
            const month = parseInt(expiration.substring(4, 6)) - 1;
            const day = parseInt(expiration.substring(6, 8));
            return new Date(year, month, day);
        } else if (/^\d{6}$/.test(expiration)) {
            const year = 2000 + parseInt(expiration.substring(0, 2));
            const month = parseInt(expiration.substring(2, 4)) - 1;
            const day = parseInt(expiration.substring(4, 6));
            return new Date(year, month, day);
        } else {
            const d = new Date(expiration);
            return isNaN(d.getTime()) ? null : d;
        }
    } catch (e) {
        return null;
    }
}

function formatDateToAPIfmt(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}${month}${day}`;
}

function formatExpirationDisplay(expiration) {
    if (!expiration) return '-';
    const exp = expiration.toString();
    if (exp.length >= 8 && /^\d{8}$/.test(exp)) {
        return `${exp.substring(0, 4)}-${exp.substring(4, 6)}-${exp.substring(6, 8)}`;
    }
    return expiration;
}

function addDaysToDate(date, days) {
    const result = new Date(date);
    result.setDate(result.getDate() + days);
    return result;
}

function findClosestStrike(strikes, targetStrike) {
    if (!strikes || strikes.length === 0) return null;
    return strikes.reduce((prev, curr) =>
        Math.abs(curr - targetStrike) < Math.abs(prev - targetStrike) ? curr : prev
    );
}

function isValidStrike(value) {
    return !isNaN(parseFloat(value));
}

function calculatePercentDifference(stockPrice, strike, optionType) {
    if (!stockPrice || !strike) return { difference: 0, percentDifference: 0 };
    const isCall = optionType === 'CALL' || optionType === 'C' || optionType === 'Call';
    const difference = isCall ? strike - stockPrice : stockPrice - strike;
    const percentDifference = (difference / strike) * 100;
    return { difference, percentDifference };
}

export {
    formatPercentage,
    formatDate,
    getBadgeColor,
    calculateMidPrice,
    calculateTargetStrike,
    roundStrikeToNearestHalf,
    parseExpirationDate,
    formatDateToAPIfmt,
    formatExpirationDisplay,
    addDaysToDate,
    findClosestStrike,
    isValidStrike,
    calculatePercentDifference,
};
