import threading
from reactor import Reactor

class System:

    def __init__(self) -> None:
        self.reactor = Reactor(9, 9, 9, 0.4, 0.4, 0.4)
    
    def main(self):
        """
        import appropriate libraries
        set up four threads to which I can assign work
        """
        threads = [
            threading.Thread(target=self.run_reactor(1), args=(1,)),
            threading.Thread(target=self.run_io(2), args=(2,)),
        ]
        for t in threads:
            t.start()
        # for i in range(4):
        #     t = threading.Thread(target=self.do_work, args=(i,))
        #     threads.append(t)
        #     t.start()
    
    def run_io(self, thread_num):
        print(f"Thread {thread_num} is running the I/O.")

    def run_reactor(self, thread_num):
        print(f"Thread {thread_num} is running the reactor.")
        self.reactor.build_and_solve_eigenvalue()

if __name__ == "__main__":
    system = System()
    system.main()

        