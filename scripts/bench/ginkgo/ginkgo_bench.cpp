// Ginkgo benchmark on one exported FEM system (MatrixMarket A and b from
// profile_fem_run.py --dump).  Usage:
//   ginkgo_bench <A.mtx> <b.mtx> <exec: cuda|omp|reference> <method> [repeat]
// methods: gmres_bj      GMRES(100) + block-Jacobi (max block 8)
//          gmres_parilu  GMRES(100) + ParILU(0) preconditioner
//          direct_lu     experimental sparse direct LU (symbolic on host)
// Prints one JSON line: setup/solve times (median), iterations, residual.
// Written against Ginkgo 1.9/1.10; adjust builder syntax if your version differs.
#include <ginkgo/ginkgo.hpp>
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

using mtx = gko::matrix::Csr<double, int>;
using vec = gko::matrix::Dense<double>;
using clk = std::chrono::steady_clock;

static double secs(clk::time_point a, clk::time_point b) {
    return std::chrono::duration<double>(b - a).count();
}

int main(int argc, char** argv) {
    if (argc < 5) { std::cerr << "usage: A.mtx b.mtx exec method [repeat]\n"; return 2; }
    std::string ex = argv[3], method = argv[4];
    int repeat = argc > 5 ? std::stoi(argv[5]) : 5;
    std::shared_ptr<gko::Executor> exec;
    auto host = gko::OmpExecutor::create();
    if (ex == "cuda") exec = gko::CudaExecutor::create(0, host);
    else if (ex == "omp") exec = host;
    else exec = gko::ReferenceExecutor::create();

    std::ifstream fa(argv[1]), fb(argv[2]);
    auto t0 = clk::now();
    auto A = gko::share(gko::read<mtx>(fa, exec));
    auto b_all = gko::read<vec>(fb, exec);
    exec->synchronize();
    double t_h2d = secs(t0, clk::now());
    auto n = A->get_size()[0];
    // first rhs column only
    auto b = gko::share(vec::create(exec, gko::dim<2>{n, 1}));
    b->copy_from(b_all->create_submatrix(gko::span{0, n}, gko::span{0, 1}));
    auto x = vec::create(exec, gko::dim<2>{n, 1});

    auto crit_it = gko::stop::Iteration::build().with_max_iters(5000u);
    auto crit_res = gko::stop::ResidualNorm<double>::build()
                        .with_baseline(gko::stop::mode::rhs_norm)
                        .with_reduction_factor(1e-10);
    std::shared_ptr<gko::LinOpFactory> factory;
    if (method == "gmres_bj") {
        factory = gko::solver::Gmres<double>::build()
                      .with_krylov_dim(100u)
                      .with_criteria(crit_it, crit_res)
                      .with_preconditioner(gko::preconditioner::Jacobi<double, int>::build()
                                               .with_max_block_size(8u))
                      .on(exec);
    } else if (method == "gmres_parilu") {
        factory = gko::solver::Gmres<double>::build()
                      .with_krylov_dim(100u)
                      .with_criteria(crit_it, crit_res)
                      .with_preconditioner(gko::preconditioner::Ilu<>::build()
                                               .with_factorization(gko::factorization::ParIlu<double, int>::build()))
                      .on(exec);
    } else if (method == "direct_lu") {
        factory = gko::experimental::solver::Direct<double, int>::build()
                      .with_factorization(gko::experimental::factorization::Lu<double, int>::build())
                      .on(exec);
    } else { std::cerr << "unknown method\n"; return 2; }

    auto logger = gko::share(gko::log::Convergence<double>::create());
    std::vector<double> setup, solve;
    int iters = -1;
    for (int r = 0; r < repeat; ++r) {
        x->fill(0.0);
        exec->synchronize();
        auto a = clk::now();
        auto solver = factory->generate(A);
        exec->synchronize();
        auto m = clk::now();
        solver->add_logger(logger);
        solver->apply(b, x);
        exec->synchronize();
        auto e = clk::now();
        setup.push_back(secs(a, m)); solve.push_back(secs(m, e));
        iters = static_cast<int>(logger->get_num_iterations());
    }
    // FP64 residual ||b - A x|| / ||b||
    auto one = gko::initialize<vec>({1.0}, exec);
    auto neg = gko::initialize<vec>({-1.0}, exec);
    auto r = gko::clone(b);
    A->apply(neg, x, one, r);
    auto nr = gko::initialize<vec>({0.0}, host), nb = gko::initialize<vec>({0.0}, host);
    auto nr_d = vec::create(exec, gko::dim<2>{1, 1}), nb_d = vec::create(exec, gko::dim<2>{1, 1});
    r->compute_norm2(nr_d); b->compute_norm2(nb_d);
    nr->copy_from(nr_d); nb->copy_from(nb_d);
    std::sort(setup.begin(), setup.end()); std::sort(solve.begin(), solve.end());
    std::cout << "{\"n\":" << n << ",\"nnz\":" << A->get_num_stored_elements()
              << ",\"exec\":\"" << ex << "\",\"method\":\"" << method << "\""
              << ",\"read_h2d_s\":" << t_h2d
              << ",\"setup_s\":" << setup[setup.size() / 2]
              << ",\"solve_s\":" << solve[solve.size() / 2]
              << ",\"iters\":" << iters
              << ",\"rel_residual\":" << nr->at(0, 0) / nb->at(0, 0) << "}" << std::endl;
    return 0;
}
