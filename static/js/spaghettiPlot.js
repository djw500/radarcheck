// Spaghetti Plot
let spaghettiChart = null;

function createSpaghettiPlot() {
    if (spaghettiChart) {
        spaghettiChart.destroy();
    }
    
    const ctx = document.getElementById('spaghettiChart').getContext('2d');
    
    // Prepare datasets
    const datasets = [];
    const colors = ['#004080', '#008000', '#800000', '#808000', '#800080'];
    
    // Create a dataset for each run
    timelineData.forEach((run, index) => {
        // Get valid times for this run
        const runValidTimes = validTimes[run.run_id] || [];
        
        // Sort by valid time
        runValidTimes.sort((a, b) => new Date(a.valid_time) - new Date(b.valid_time));
        
        // Create dataset
        datasets.push({
            label: new Date(run.init_time).toLocaleString(),
            data: runValidTimes.map(vt => ({
                x: new Date(vt.valid_time),
                y: Math.random() * 100  // Placeholder for actual precipitation data
            })),
            borderColor: colors[index % colors.length],
            backgroundColor: 'transparent',
            tension: 0.4
        });
    });
    
    spaghettiChart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: 'hour',
                        displayFormats: {
                            hour: 'MM/dd HH:mm'
                        }
                    },
                    title: {
                        display: true,
                        text: 'Valid Time'
                    }
                },
                y: {
                    title: {
                        display: true,
                        text: 'Precipitation Intensity (simulated)'
                    },
                    min: 0,
                    max: 100
                }
            },
            plugins: {
                title: {
                    display: true,
                    text: 'Precipitation Forecast Comparison'
                },
                tooltip: {
                    mode: 'index',
                    intersect: false
                }
            }
        }
    });
}
