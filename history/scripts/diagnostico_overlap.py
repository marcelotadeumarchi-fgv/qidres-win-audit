import sys; sys.path.insert(0,'/home/marchi/financial_ai_project/code/b3_data/qid')
import protocolo_estimar as P, polars as pl, numpy as np, statsmodels.api as sm
d=P.carregar(False); g=P.agregar(d,5)
print("DIAGNOSTIC (not a test of H1): why did the earlier exploratory z=2.644 vanish?")
print("Isolating ONE difference at a time, 5-min windows, training days.\n")
print(f"  {'price basis for the return':<40}{'beta':>12}{'z':>9}{'p':>10}{'R2':>9}")
print("-"*80)
for rot,col in (("close-to-close (PROTOCOL §3.5)","mp"),
                ("close-to-close, L1 midpoint","mid"),
                ("window MEAN midpoint (old exploratory)","avg_mid")):
    gg=g.with_columns(ret=((pl.col(col).shift(-1)-pl.col(col))/pl.col(col)*10000))
    gg=gg.with_columns(df=pl.col("dia").shift(-1)).filter((pl.col("df")==pl.col("dia"))&pl.col("ret").is_not_null()&pl.col("qid_signed").is_finite())
    X=P.desenho(gg); y=gg["ret"].to_numpy()
    f=sm.OLS(y,X).fit(cov_type="HAC",cov_kwds={"maxlags":2})
    print(f"  {rot:<40}{f.params[1]:>12.2f}{f.tvalues[1]:>9.3f}{f.pvalues[1]:>10.5f}{100*f.rsquared:>8.3f}%")
print("-"*80)
print("\n  A window MEAN price overlaps the window in which the signal is measured;")
print("  a close-to-close return does not. This is exactly the contemporaneity")
print("  leak that thesis §6.2 warns about and the protocol §3.5 guards against.")
