// View switching
function initViewSwitching() {
    const viewButtons = document.querySelectorAll('.view-selector button');
    const views = document.querySelectorAll('.view');
    
    viewButtons.forEach(button => {
        button.addEventListener('click', () => {
            // Deactivate all buttons and views
            viewButtons.forEach(b => b.classList.remove('active'));
            views.forEach(v => v.classList.remove('active'));
            
            // Activate the clicked button and corresponding view
            button.classList.add('active');
            const viewId = button.id.replace('Btn', '');
            document.getElementById(viewId).classList.add('active');
            
            // Initialize the view if needed
            if (viewId === 'timelineView') {
                createTimeline();
            } else if (viewId === 'spaghettiView') {
                createSpaghettiPlot();
            }
        });
    });
}
