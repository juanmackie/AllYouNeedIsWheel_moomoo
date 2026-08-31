/**
 * Sparklines Module
 * Lightweight inline SVG sparklines (no dependencies)
 */

/**
 * Create an inline SVG sparkline
 * @param {HTMLElement} container - Container to append the SVG to
 * @param {number[]} data - Array of numbers to plot
 * @param {Object} options - Optional config {width, height, color, fill}
 */
export function createSparkline(container, data, options = {}) {
    if (!container || !data || data.length < 2) return;

    const width = options.width || 100;
    const height = options.height || 30;
    const color = options.color || 'auto';  // 'auto' uses green/red based on trend
    const fill = options.fill !== undefined ? options.fill : false;
    const strokeWidth = options.strokeWidth || 1.5;

    // Determine color based on trend
    let lineColor = color;
    if (color === 'auto') {
        const first = data[0];
        const last = data[data.length - 1];
        lineColor = last >= first ? '#198754' : '#dc3545';  // green : red
    }

    // Calculate min/max for scaling
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;  // Avoid division by zero

    // Generate points
    const points = data.map((value, index) => {
        const x = (index / (data.length - 1)) * width;
        const y = height - ((value - min) / range) * height;
        return `${x},${y}`;
    }).join(' ');

    // Create SVG
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('width', width);
    svg.setAttribute('height', height);
    svg.style.cssText = 'display: block;';

    if (fill) {
        // Filled sparkline
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        path.setAttribute('points', points);
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', lineColor);
        path.setAttribute('stroke-width', strokeWidth);
        path.setAttribute('stroke-linecap', 'round');
        path.setAttribute('stroke-linejoin', 'round');
        svg.appendChild(path);

        // Add fill area
        const fillPath = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
        fillPath.setAttribute('points', `0,${height} ${points} ${width},${height}`);
        fillPath.setAttribute('fill', lineColor);
        fillPath.setAttribute('opacity', '0.1');
        svg.appendChild(fillPath);
    } else {
        // Simple stroke only
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        path.setAttribute('points', points);
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', lineColor);
        path.setAttribute('stroke-width', strokeWidth);
        path.setAttribute('stroke-linecap', 'round');
        path.setAttribute('stroke-linejoin', 'round');
        svg.appendChild(path);
    }

    container.appendChild(svg);
    return svg;
}
