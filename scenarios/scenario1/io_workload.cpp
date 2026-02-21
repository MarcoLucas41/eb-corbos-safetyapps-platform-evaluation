#include <iostream>
#include <fstream>
#include <vector>
#include <atomic>
#include <csignal>
#include <cstring>
#include <random>
#include <chrono>
#include <thread>
#include <iomanip>
#include <unistd.h>
#include <fcntl.h>

// Configuration
std::atomic<bool> g_running(true);

void signal_handler(int) {
    std::cout << "\nShutting down I/O workload..." << std::endl;
    g_running = false;
}

// I/O-intensive workload with continuous writes
void io_intensive_workload(size_t write_rate_mbs) {
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<uint8_t> byte_dist(0, 255);
    
    const size_t BUFFER_SIZE = 1024 * 1024; // 1MB buffer
    std::vector<uint8_t> buffer(BUFFER_SIZE);
    
    // Fill buffer with random data
    for (size_t i = 0; i < BUFFER_SIZE; ++i) {
        buffer[i] = byte_dist(gen);
    }
    
    std::string filename = "io_workload_temp.dat";
    uint64_t total_written = 0;
    uint64_t iteration = 0;
    
    std::cout << "Target write rate: " << write_rate_mbs << " MB/s" << std::endl;
    std::cout << "Writing to: " << filename << std::endl;
    
    while (g_running) {
        auto start_time = std::chrono::steady_clock::now();
        
        // Open file for writing
        int fd = open(filename.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd < 0) {
            std::cerr << "Failed to open file: " << filename << std::endl;
            break;
        }
        
        // Calculate how much to write this iteration
        size_t bytes_to_write = write_rate_mbs * BUFFER_SIZE;
        size_t bytes_written_iteration = 0;
        
        while (bytes_written_iteration < bytes_to_write && g_running) {
            ssize_t written = write(fd, buffer.data(), BUFFER_SIZE);
            if (written < 0) {
                std::cerr << "Write error" << std::endl;
                break;
            }
            bytes_written_iteration += written;
            total_written += written;
            
            // Force sync to disk periodically for maximum I/O pressure
            if (bytes_written_iteration % (10 * BUFFER_SIZE) == 0) {
                fsync(fd);
            }
        }
        
        // Final sync
        fsync(fd);
        close(fd);
        
        iteration++;
        if (iteration % 10 == 0) {
            double mb_written = total_written / (1024.0 * 1024.0);
            std::cout << "I/O workload: " << iteration << " iterations, " 
                      << std::fixed << std::setprecision(2) << mb_written << " MB written" 
                      << std::endl;
        }
        
        // Sleep to maintain target rate
        auto end_time = std::chrono::steady_clock::now();
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
        auto target_duration = std::chrono::milliseconds(1000); // 1 second per iteration
        
        if (elapsed < target_duration) {
            std::this_thread::sleep_for(target_duration - elapsed);
        }
    }
    
    // Cleanup
    unlink(filename.c_str());
}

int main(int argc, char* argv[]) {
    std::cout << "I/O-Intensive Workload" << std::endl;
    std::cout << "======================" << std::endl;
    
    size_t write_rate_mbs = 10;  // Default 10 MB/s
    
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "-r" || arg == "--rate") {
            if (i + 1 < argc) {
                write_rate_mbs = std::atoi(argv[++i]);
                if (write_rate_mbs < 1) write_rate_mbs = 1;
                if (write_rate_mbs > 1000) write_rate_mbs = 1000;
            }
        } else if (arg == "-h" || arg == "--help") {
            std::cout << "\nUsage: " << argv[0] << " [options]\n"
                      << "Options:\n"
                      << "  -r, --rate <MB/s>  Write rate in MB/s (default: 10, max: 1000)\n"
                      << "  -h, --help         Show this help message\n"
                      << "\nGenerates I/O interference through continuous file writes with fsync.\n"
                      << std::endl;
            return 0;
        }
    }
    
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    io_intensive_workload(write_rate_mbs);
    
    std::cout << "I/O workload terminated." << std::endl;
    return 0;
}
