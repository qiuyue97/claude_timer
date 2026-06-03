"""Fake interactive program for PTY driver tests: banner, reply, exit on /exit."""
import sys


def main():
    sys.stdout.write("Fake Claude ready\n")
    sys.stdout.flush()
    sys.stdin.readline()  # the ping message
    sys.stdout.write("Reply: received your message ok\n")
    sys.stdout.flush()
    for line in sys.stdin:
        if line.strip() == "/exit":
            break
    sys.stdout.write("exiting\n")
    sys.stdout.flush()


main()
