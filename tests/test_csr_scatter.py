from concurrent.futures import ThreadPoolExecutor
import numpy as np
from scipy.sparse import coo_matrix

from motor_ai_sim.simulation import csr_scatter as m

def reference(idx,values,shape):
    a=coo_matrix((values,(idx[0],idx[1])),shape=shape);a.eliminate_zeros();return a.tocsr()

def check(a,b):
    assert np.array_equal(a.indptr,b.indptr)
    assert np.array_equal(a.indices,b.indices)
    assert np.array_equal(a.data,b.data)

def test_zeros_and_cancellation_keep_original_structure():
    idx=np.array([[0,0,0,1,2],[1,1,2,1,2]])
    s=m.CsrScatter(idx,(3,3))
    for data in ([1.,-1.,0.,0.,2.],[0.,0.,0.,0.,0.],[2.,3.,0.,4.,0.]):
        check(reference(idx,data,(3,3)),s.assemble(data))

def test_random_duplicates_empty_rows_and_output_buffer_ownership():
    rng=np.random.default_rng(12);idx=rng.integers(0,8,(2,300));idx[0]%=6
    s=m.CsrScatter(idx,(9,9));v=rng.standard_normal(300);v[::3]=0
    a=s.assemble(v);saved=a.copy();b=s.assemble(v*2)
    b.indices[:]=0;b.indptr[:]=0;b.data[:]=123
    check(saved,a);check(reference(idx,v,(9,9)),s.assemble(v))

def test_shared_scatter_concurrent_values_are_independent():
    rng=np.random.default_rng(8);idx=rng.integers(0,20,(2,400));s=m.CsrScatter(idx,(20,20))
    values=[rng.standard_normal(400) for _ in range(16)]
    with ThreadPoolExecutor(4) as pool:results=list(pool.map(s.assemble,values))
    for v,a in zip(values,results):check(reference(idx,v,(20,20)),a)

def test_zero_mask_cache_overflow_preserves_exact_duplicate_sum_order():
    rng=np.random.default_rng(432)
    idx=rng.integers(0,12,(2,1000));s=m.CsrScatter(idx,(12,12))
    # Widely differing magnitudes expose any reordering of duplicate sums.
    original=rng.standard_normal(1000)*np.power(10.,rng.integers(-12,13,1000))
    for i in range(8):
        v=original.copy();v[rng.random(1000)<.3]=0.
        check(reference(idx,v,(12,12)),s.assemble(v))
        assert len(s._plans)<=2

def test_already_sorted_duplicates_are_not_resorted():
    rng=np.random.default_rng(1)
    idx=np.array([np.zeros(1000,dtype=int),np.repeat(np.arange(10),100)])
    v=rng.standard_normal(1000)*np.power(10.,rng.integers(-12,13,1000))
    check(reference(idx,v,(10,10)),m.CsrScatter(idx,(10,10)).assemble(v))
