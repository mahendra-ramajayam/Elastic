#!/usr/bin/python

#
#    Copyright 1998-2011 by Paweł T. Jochym <pawel.jochym@ifj.edu.pl>
#
#    This file is part of Elastic.

#    Elastic is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    Elastic is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.

#    You should have received a copy of the GNU General Public License
#    along with Elastic.  If not, see <http://www.gnu.org/licenses/>.


import re
import sys
import string


import ase.io
from ase.atoms import Atoms
from pyspglib import spglib as spg
from pvasp import ParCalculate
from scipy.linalg import norm, lstsq
from numpy.linalg import inv
from numpy import dot, diag, ones, reshape
from math import acos, pi, cos, sin, tan
import ase.units as units


def BMEOS(v,v0,b0,b0p):
    return (b0/b0p)*(pow(v0/v,b0p) - 1)

def ctg(x):
    return cos(x)/sin(x)

def csc(x):
    return 1/sin(x)


def regular(u):
    uxx, uyy, uzz, uyz, uxz, uxy = u[0],u[1],u[2],u[3],u[4],u[5]
    return array([
    [uxx,     uyy + uzz,      0],
    [uyy,   uxx + uzz,      0],
    [uzz,   uxx + uyy,      0],
    [0,             0,              2*uyz],
    [0,             0,              2*uxz],
    [0,             0,              2*uxy]])



class Crystal(Atoms):

    def __init__(self, *args, **kwargs):
        Atoms.__init__(self, *args, **kwargs)
        self.recalc_bulk=True
        self.bulk_modulus=None
        self.bm_eos=None
        
    ls=[
        [3,   "Triclinic"],
        [16,  "Monoclinic"],
        [75,  "Orthorombic"],
        [143, "Tetragonal"],
        [168, "Trigonal"],
        [195, "Hexagonal"],
        [231, "Cubic"]
    ]
    

    sij={
#        "Triclinic": array(),
#        "Monoclinic": array(),
#        "Orthorombic": array(),
#        "Tetragonal": array(),
#        "Trigonal": array(),
#        "Hexagonal": array(),
        "Cubic": regular
    }


    def get_lattice_type(self):
        sg=spg.get_spacegroup(self)
        m=re.match('([A-Z].*\\b)\s*\(([0-9]*)\)',sg)
        self.sg_name=m.group(1)
        self.sg_nr=string.atoi(m.group(2))
        
        for n,l in enumerate(Crystal.ls) :
            if self.sg_nr < l[0] :
                lattice=l[1]
                lattype=n+1
                break
        self.sg_type=lattype
        self.bravais=lattice
        return lattype
        
    def get_bulk_modulus(self,n=5, lo=0.98, hi=1.02, recalc=False):
        """Calculate bulk modulus."""
        if self._calc is None:
            raise RuntimeError('Crystal object has no calculator.')

        if self.bm_eos == None :
            self.get_BM_EOS(n,lo,hi,recalc)
        self.bulk_modulus=self.bm_eos[1]
        return self.bulk_modulus
        
    def get_pressure(self,s):
        '''
        Return isotropic (hydrostatic) pressure in ASE units
        instead of bar. Convenience function.
        '''
        return 1e5*units.Pascal*self.get_isotropic_pressure(s)
        
    def get_BM_EOS(self,n=5, lo=0.98, hi=1.02, recalc=False):
        """Calculate Birch-Murnaghan EOS."""
        if self._calc is None:
            raise RuntimeError('Crystal object has no calculator.')

        if self.bm_eos==None or recalc :
            # NOTE: The calculator should properly minimize the energy
            # at each volume by optimizing the internal degrees of freedom
            # in the cell and/or cell shape without touching the volume.
            # TODO: Provide api for specifying IDOF and Full optimization 
            #       calculators. Maybe just calc_idof and calc_full members?
            res=ParCalculate(self.scan_volumes(lo,hi,n),self.calc)
            
            pvdat=array([[r.get_volume(),
                            self.get_pressure(r.get_stress()),
                            norm(r.get_cell()[:,0]),
                            norm(r.get_cell()[:,1]),
                            norm(r.get_cell()[:,2])] for r in res])
            #print pvdat

            # Fitting functions
            fitfunc = lambda p, x: [BMEOS(xv,p[0],p[1],p[2]) for xv in x]
            errfunc = lambda p, x, y: fitfunc(p, x) - y

            # Estimate the initial guess assuming b0p=1
            # Limiting volumes
            v1=min(pvdat[:,0])
            v2=max(pvdat[:,0])
            # The pressure is falling with the growing volume
            p2=min(pvdat[:,1])
            p1=max(pvdat[:,1])
            b0=(p1*v1-p2*v2)/(v2-v1)
            v0=v1*(p1+b0)/b0
            # Initial guess
            p0=[v0,b0,1]
            #Fitting
            #print p0
            p1, succ = optimize.leastsq(errfunc, p0[:], args=(pvdat[:,0],pvdat[:,1]))
            if not succ :
                raise RuntimeError('Calculation failed')
            self.bm_eos=p1
            self.pv=pvdat
        return self.bm_eos

    def get_elastic_tensor(self, n=5, d=2):
        '''
        Calculate elastic tensor of the crystal.
        It is assumed that the crystal is converged and optimized 
        under intended pressure/stress.
        The geometry and stress at the call point is taken as
        the reference point. No additional optimization will be run.
        It is also assumed that the calculator is set to pure IDOF optimization.
        '''
        # TODO: Provide API to enforce calculator selection
        
        # Decide which deformations should be used
        axis=[]
        if self.bravais=="Cubic" :
            axis=[0,3]
            symm=regular
        else :
            1/0
        # Generate deformations
        sys=[]
        for a in axis :
            if a <3 : # tetragonal deformation
                for dx in linspace(-d,d,n):
                    sys.append(self.get_cart_deformed_cell(axis=a, size=dx))
            elif a<6 : # sheer deformation (skip the zero angle)
                for dx in linspace(d/10.0,d,n):
                    sys.append(self.get_cart_deformed_cell(axis=a, size=dx))
        
        # Run the calculation
        r=ParCalculate(sys,self.calc)
        
        ul=[]
        sl=[]
        p=self.get_pressure(self.get_stress())
        p=0
        for g in r:
            ul.append(g.get_strain(self))
            # Remove the pressure from the stress tensor
            sl.append(g.get_stress()-array([p,p,p,0,0,0]))
        eqm=array(map(symm,ul))
        #print eqm[0].shape, eqm.shape
        eqm=reshape(eqm,(eqm.shape[0]*eqm.shape[1],eqm.shape[2]))
        #print eqm
        slm=reshape(array(sl),(-1,))
        #print eqm.shape, slm.shape
        #print slm
        Bij = lstsq(eqm,slm)
        #print Bij[0] / units.GPa
        return Bij
    
    def scan_pressures(self, lo, hi, n=5):
        '''
        Scan the pressure axis from lo to hi (inclusive) 
        using B-M EOS as the volume predictor.
        Pressure (lo, hi) in GPa
        '''
        # Inverse B-M EOS to get volumes from pressures
        # This will work only in limited pressure range p>-B/B'.
        # Warning! Relative, the V0 prefactor is removed.
        invbmeos = lambda b, bp, x: array([pow(b/(bp*xv+b),1/(3*bp)) for xv in x])
        
        eos=self.get_BM_EOS()
        
        # Limit negative pressures to 90% of the singularity value.
        # Beyond this B-M EOS is bound to be wrong anyway.
        lo=max(lo,-0.9*eos[1]/eos[2])
        
        scale=(eos[0]/self.get_volume())*invbmeos(eos[1], eos[2], 
                                                    linspace(lo,hi,num=n))
        #print scale
        uc=self.get_cell()
        sys=[Atoms(self) for s in scale]
        for n, s in enumerate(scale):
            sys[n].set_cell(s*uc,scale_atoms=True)
        
        return sys
        
        
    def scan_volumes(self, lo, hi, n):
        '''
        Provide set of crystals along volume axis from lo to hi (inclusive).
        No volume cell optimization is performed. Bounds are specified as 
        fractions (1.10 = 10% increase).
        '''
        scale=linspace(lo,hi,num=n)
        uc=self.get_cell()
        sys=[Atoms(self) for s in scale]
        for n, s in enumerate(scale):
            sys[n].set_cell(s*uc,scale_atoms=True)
        
        return sys

        
    def get_vecang_cell(self, uc=None):
        '''
        Compute A,B,C, alpha,beta,gamma cell params
        from the unit cell matrix (uc) or self.
        Angles in radians.
        '''
        if uc==None :
            uc=self.get_cell()
        ucv=[uc[i,:]/norm(uc[i,:]) for i in range(3)]
        uca=[acos(dot(ucv[(i+1)%3],ucv[(i+2)%3])) for i in range(3)]
        return [norm(uc[i,:]) for i in range(3)] + uca
        
    def get_deformed_cell(self, axis=0, size=1):
        '''
        Return the cell (with atoms) deformed along one
        cell parameter (0,1,2 = a,b,c ; 3,4,5 = alpha,beta,gamma) by
        size percent or size degrees (axis/angles).
        '''
        cryst=Crystal(self)
        if axis < 3 :
            uc[axis,:]=(1+size/100.0)*uc[axis,:]
        else :
            (a,b,c,alp,bet,gam)=cryst.get_vecang_cell()
            d=array([0.0, 0.0, 0.0])
            d[axis-3]=pi*size/180
            (alp,bet,gam)=array((alp,bet,gam))+d
            t=1 - (ctg(bet)*ctg(gam)-cos(alp)*csc(bet)*csc(gam))**2;
            if t<0.0 :
                print '''
                The parameters (alpha,beta,gamma)=(%f,%f,%f) are probably 
                incorrect and lead to imaginary coordinates. 
                This range of parameters is unsupported by this program 
                (and is, let me say, very strange for a crystal).
                Cennot continue, bye.''' % (alp,bet,gam)
                raise ValueError
            else :
                uc=[[a,0.0,0.0],
                    [b*cos(gam), b*sin(gam), 0],
                    [c*cos(bet), 
                        c*(cos(alp)/sin(gam) - cos(bet)*ctg(gam)),
                        c*sin(bet)*sqrt(t)]]
        cryst.set_cell(uc, scale_atoms=True)
        #print cryst.get_cell()
        #print uc
        return cryst

    def get_cart_deformed_cell(self, axis=0, size=1):
        '''
        Return the cell (with atoms) deformed along one 
        of the cartesian directions 
        (0,1,2 = x,y,z ; sheers: 3,4,5 = yz, xz, xy) by
        size percent.
        '''
        cryst=Crystal(self)
        uc=cryst.get_cell()
        l=size/100.0
        L=diag(ones(3))
        if axis < 3 :
            L[axis,axis]+=l
        else :
            if axis==3 :
                L[1,2]+=l
            elif axis==4 :
                L[0,2]+=l
            else :
                L[0,1]+=l
        uc=dot(uc,L)
        cryst.set_cell(uc, scale_atoms=True)
        #print cryst.get_cell()
        #print uc
        return cryst
        
    def get_strain(self,refcell=None):
        if refcell==None :
            refcell=self
        du=self.get_cell()-refcell.get_cell()
        m=refcell.get_cell()
        m=inv(m)
        u=dot(m,du)
        u=(u+u.T)/2
        return array([u[0,0], u[1,1], u[2,2], u[2,1], u[2,0], u[1,0]])


if __name__ == '__main__':

# Test case for the code. Calculate few well-known crystals

    import os
    from numpy import linspace, array, arange
    import numpy
    from scipy import stats, optimize
    from math import pow

    from matplotlib.pyplot import plot, show, figure, draw, axvline, axhline
    from ase.lattice.spacegroup import crystal
    from ase.visualize import view
    from pvasp import ClusterVasp


# You can specify the directory with coerged VASP crystal for the test run
# or run through all prepared cases.
    if len(sys.argv)>1 :
        crystals=[Crystal(ase.io.read(sys.argv[1]+'/CONTCAR'))]
    else :
        # Pre-cooked test cases
        crystals=[]
        
        # Cubic
        a = 4.194
        crystals.append(Crystal(crystal(['Mg', 'O'], [(0, 0, 0), (0.5, 0.5, 0.5)],
            spacegroup=225, cellpar=[a, a, a, 90, 90, 90])))
#        a = 4.194
#        crystals.append(Crystal(crystal(['Ti', 'C'], [(0, 0, 0), (0.5, 0.5, 0.5)],
#            spacegroup=225, cellpar=[a, a, a, 90, 90, 90])))
        # Tetragonal
        a = 4.60
        c = 2.96
        crystals.append(Crystal(crystal(['Ti', 'O'], [(0, 0, 0), (0.302, 0.302, 0)],
            spacegroup=136, cellpar=[a, a, c, 90, 90, 90])))
        # Trigonal
        a = 4.48
        c = 11.04
        crystals.append(Crystal(crystal(['Sb'], [(0, 0, 0.24098)],
            spacegroup=166, cellpar=[a, a, c, 90, 90, 120])))


    print "Running tests"
    # Iterate over all crystals. 
    # We do not paralelize over test cases for clarity.
    for cryst in crystals[:1] :

        
        # Setup the calculator
        calc=ClusterVasp(nodes=1,ppn=8)
        cryst.set_calculator(calc)
        calc.set(prec = 'Accurate', 
                    xc = 'PBE', 
                    lreal = False, 
                    isif=4, 
                    nsw=30,
                    ediff=1e-8, 
                    ibrion=2)
        calc.set(kpts=[3,3,3])
        
        # Run the calculations
        
        # Optimize the structure first
        calc.set(isif=3)
        
        # Run the internal optimizer
        print "Residual pressure: %.3f GPa" % (
                    cryst.get_pressure(cryst.get_stress())/units.GPa)
        print "Residual stress (GPa):", cryst.get_stress()/units.GPa

        calc.clean()
        cryst.get_lattice_type()

        print cryst.get_vecang_cell()
        print cryst.bravais, cryst.sg_type, cryst.sg_name, cryst.sg_nr
        
        #view(cryst)

        # Switch to cell shape+IDOF optimizer
        calc.set(isif=4)
        
        # Calculate few volumes and fit B-M EOS to the result
        # Use default +/-2% volume deformation and 5 data points
        fit=cryst.get_BM_EOS(n=5,lo=0.97,hi=1.03)
        
        # Get the P(V) data points just calculated
        pv=array(cryst.pv)
        
        # Sort data on the first column (V)
        pv=pv[pv[:,0].argsort()]
        
        # Print the fitted parameters
        print "V0=%.3f A^3 ; B0=%.2f GPa ; B0'=%.3f ; a0=%.5f A" % ( 
                fit[0], fit[1]/units.GPa, fit[2], pow(fit[0],1./3))
                
        v0=fit[0]

        # B-M EOS for plotting
        fitfunc = lambda p, x: [BMEOS(xv,p[0],p[1],p[2]) for xv in x]

        # Ranges - the ordering in pv is not guarateed at all!
        # In fact it may be purely random.
        x=array([min(pv[:,0]),max(pv[:,0])])
        y=array([min(pv[:,1]),max(pv[:,1])])

        
        # Plot b/a and c/a as a function of v/v0
        figure(1)
        plot(pv[:,0]/v0,pv[:,3]/pv[:,2],'o')
        plot(pv[:,0]/v0,pv[:,4]/pv[:,2],'x-')
        #print pv[:,4]/pv[:,2]
        axvline(1,ls='--')
        draw()
        
        # Plot the P(V) curves and points for the crystal
        figure(2)
        # Plot the points
        plot(pv[:,0]/v0,pv[:,1],'o')
        
        # Mark the center P=0 V=V0
        axvline(1,ls='--')
        axhline(0,ls='--')

        # Plot the fitted B-M EOS through the points
        xa=linspace(x[0],x[-1],20)
        #xa=v0*linspace(0.90,1.10,20)
        plot(xa/v0,fitfunc(fit,xa),'-')
        draw()
        
        # Scan over deformations
        
        # Switch to IDOF optimizer
        calc.set(isif=2)

        # Elastic tensor by internal routine
        Bij=cryst.get_elastic_tensor(n=8,d=0.33)
        print "Bij (GPa)", Bij[0]/units.GPa
        # Now let us do it (only c11 and c12) by hand
        sys=[]
        for d in linspace(-0.5,0.5,6):
            sys.append(cryst.get_cart_deformed_cell(axis=0,size=d))
        r=ParCalculate(sys,cryst.calc)
        ss=[]
        for s in r:
            ss.append([s.get_strain(cryst), s.get_stress()])

        # Plot strain-stress relation
        figure(3)

        ss=[]
        for p in r:
            ss.append([p.get_strain(cryst),p.get_stress()])
        ss=array(ss)
        lo=min(ss[:,0,0])
        hi=max(ss[:,0,0])
        mi=(lo+hi)/2
        wi=(hi-lo)/2
        xa=linspace(mi-1.1*wi,mi+1.1*wi, 50)
        plot(ss[:,0,0],ss[:,1,0],'k.')
        plot(ss[:,0,0],ss[:,1,1],'r.')
        
        # C11 component
        f=numpy.polyfit(ss[:,0,0],ss[:,1,0],3)
        c11=f[-2]/units.GPa
        plot(xa,numpy.polyval(f,xa),'b-')
        
        # C12 component
        f=numpy.polyfit(ss[:,0,0],ss[:,1,1],3)
        c12=f[-2]/units.GPa
        plot(xa,numpy.polyval(f,xa),'g-')
        print 'C11 = %.3f GPa, C12 = %.3f GPa => K= %.3f GPa' % (c11, c12, (c11+2*c12)/3)
        axvline(0,ls='--')
        axhline(0,ls='--')
        draw()

#        # Now make a short scan over pressures
#        
#        # Switch just shape+IDOF optimization
#        calc.set(isif=4)
#        sys=[]
#        sys=cryst.scan_pressures(-5.0, 5.0, 3)
#        r=ParCalculate(sys,cryst.calc)
#        print "Pressure scan (GPa):",
#        for s in r :
#            print cryst.get_pressure(s.get_stress())/units.GPa,
#        print
#        vl=array([s.get_volume() for s in r])
#        pl=array([cryst.get_pressure(s.get_stress())/units.GPa for s in r])
#        figure(2)
#        plot(vl/v0,pl,'+')
#        
#        # Check for proper inverse eos
#        invbmeos = lambda b, bp, x: array([pow(b/(bp*xv+b),1/bp) for xv in x])
#        xa=linspace(max(-8,-0.9*fit[1]/fit[2]),8,20)
#        ya=invbmeos(fit[1],fit[2],xa)
#        plot(ya,xa,'-')
    
    show()




