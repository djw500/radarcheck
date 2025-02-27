// Initialize everything when the page loads
document.addEventListener('DOMContentLoaded', function() {
    initViewSwitching();
    initSingleRunView();
    
    // Pre-create the timeline structure when the page loads
    setTimeout(createTimeline, 100);
});
