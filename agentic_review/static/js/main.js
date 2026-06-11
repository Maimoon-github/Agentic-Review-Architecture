/**
 * AgentFlow Global initialization
 */
document.addEventListener('DOMContentLoaded', () => {
    // Add subtle hover effects or global UI behaviors if needed
    console.log('AgentFlow Orchestration Engine initialized.');

    // Cleanup animations after they run
    const animatedElements = document.querySelectorAll('.animate-fade-in');
    animatedElements.forEach(el => {
        el.addEventListener('animationend', () => {
            // Optional: remove class or handle post-animation state
        });
    });
});
