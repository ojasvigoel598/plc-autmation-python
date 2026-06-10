"""
Main system integration and runtime execution hub.
"""
import matplotlib.pyplot as plt
from simulation import ( run_module_1, run_module_2, run_module_3, run_module_4, run_module_5, run_module_6 )
from plc_advanced_module import ( run_module_7,run_module_8,run_module_9,run_module_10 )
import os
import matplotlib.pyplot as plt

RESULT_DIR = "results"
os.makedirs(RESULT_DIR, exist_ok=True)

plot_counter = 0


def save_all_figures():
    """Save ALL open matplotlib figures at once"""
    global plot_counter

    figs = [plt.figure(n) for n in plt.get_fignums()]

    for fig in figs:
        plot_counter += 1
        path = os.path.join(RESULT_DIR, f"plot_{plot_counter}.png")
        fig.savefig(path, dpi=300)
        plt.close(fig)



def main():
    print("=" * 60)
    print("   LAUNCHING INTERACTIVE PLC WATER TANK AUTOMATION PLANT   ")
    print("=" * 60)
    
    # Run all modules sequentially
    run_module_1()
    run_module_2()
    run_module_3()
    run_module_4()
    run_module_5()
    run_module_6()
    run_module_7()
    run_module_8()
    run_module_9()
    run_module_10()
    
    print("\n[INFO] Sequences complete. Generating simulation output graphs...")
    plt.show()

if __name__ == "__main__":
    main()
    