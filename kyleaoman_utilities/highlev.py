import h5py
from os import path
import numpy as np
from simfiles.configs.C_EAGLE_cosma import suffix
from astropy.cosmology import Planck13 as cosmo, z_at_value
import astropy.units as U


def _replace_invalid(arr, val, rep):
    arr[arr == val] = rep
    return arr


def _to_proper(d, a):
    shape = [si if si == len(a) else 1 for si in d.shape]
    return d / a.reshape(shape)


def recentre(xyz, centre=np.zeros(3) * U.Mpc, Lbox=100 * U.Mpc):
    xyz = xyz - centre
    xyz[xyz < -Lbox / 2.] += Lbox
    xyz[xyz > Lbox / 2.] -= Lbox
    return xyz


def host_mask(HL, Mrange=(0, np.inf), snap=-1, ret_inds=False,
              ret_interp_inds=False):
    if ret_interp_inds and not ret_inds:
        raise ValueError('ret_inds required for ret_interp_inds')
    mask = np.logical_and(
        np.logical_and(
            np.logical_not(HL.SatFlag[:, snap]),
            np.logical_not(HL.ContFlag[:, snap])
        ),
        np.logical_and(
            HL.M200[:, snap] > Mrange[0],
            HL.M200[:, snap] < Mrange[1]
        )
    )
    inds = (np.where(mask)[0], ) if ret_inds else tuple()
    interp_inds = (HL.interpGalaxyRevIndex[inds], ) if ret_interp_inds \
        else tuple()
    return (mask, ) + inds + interp_inds


def sat_mask(HL, host, Rcut=3.35, snap=-1, ret_inds=False,
             ret_interp_inds=False):
    if snap < 0:
        snap = len(HL.snap_times) + snap
    if ret_interp_inds and not ret_inds:
        raise ValueError('ret_inds required for ret_interp_inds')
    xyz = recentre(HL.Centre[:, snap], host.Centre[snap], Lbox=HL.Lbox)
    cube_mask = (np.abs(xyz) < Rcut * host.R200[snap]).all(axis=-1)
    sphere_mask = np.sum(np.power(xyz[cube_mask], 2), axis=-1) \
        < np.power(Rcut * host.R200[snap], 2)
    cube_mask[cube_mask] = sphere_mask  # refine to sphere
    mask = cube_mask
    mask[host.ind] = False
    inds = (np.where(mask)[0], ) if ret_inds else tuple()
    interp_inds = (HL.interpGalaxyRevIndex[inds], ) if ret_interp_inds \
        else tuple()
    return (mask, ) + inds + interp_inds


# could/should generalize to be along a given axis
def find_peris(r, Rcut=3.35):
    minima = np.logical_and(
        np.concatenate((
            np.ones(r.shape[0])[..., np.newaxis],
            r[:, 1:] < r[:, :-1]
        ), axis=1),
        np.concatenate((
            r[:, :-1] < r[:, 1:],
            np.ones(r.shape[0])[..., np.newaxis]
        ), axis=1)
    )
    minima = np.logical_and(minima, r < Rcut)
    minima[:, -1] = False
    return minima


def t_r_firstperi(peris, t, r):
    ifirstperi, jfirstperi = np.nonzero(peris)
    jfirstperi = jfirstperi[np.r_[True, np.diff(ifirstperi) > 0]]
    ifirstperi = ifirstperi[np.r_[True, np.diff(ifirstperi) > 0]]
    tfirstperi = np.zeros(peris.shape[0]) * np.nan * t.unit
    rfirstperi = np.zeros(peris.shape[0]) * np.nan * r.unit
    tfirstperi[peris.any(axis=1)] = t[jfirstperi]
    rfirstperi[peris.any(axis=1)] = r[ifirstperi, jfirstperi]
    return tfirstperi, rfirstperi


def t_firstinfall(r, t, Rcut=3.35):
    inside = r < Rcut
    iinfall, jinfall = np.nonzero(inside)
    jinfall = jinfall[np.r_[True, np.diff(iinfall) > 0]]
    tfirstinfall = np.zeros(inside.shape[0]) * np.nan * t.unit
    tfirstinfall[inside.any(axis=1)] = t[jinfall]
    return tfirstinfall


class _Gal(object):

    def __init__(self, HL, ind, interp_ind=None):

        self.HL = HL
        self.ind = ind
        self.interp_ind = interp_ind
        return

    def __getitem__(self, k):
        if (k in self.HL.interp_keys):
            if (self.interp_ind is not None):
                retval = self.HL[k][self.interp_ind]
                shape = (np.sum(self.interp_ind == -1), 3, 1)
                retval[self.interp_ind == -1] = \
                    np.ones(shape) * np.nan
                return retval
            else:
                raise ValueError('Host requires interp_ind for interpolated'
                                 ' values.')
        elif k in (self.HL.prop_keys | self.HL.pos_keys | self.HL.snip_keys):
            return self.HL[k][self.ind]
        else:
            raise KeyError

    def __getattribute__(self, name):
        if name in object.__getattribute__(self, 'HL').pos_keys | \
           object.__getattribute__(self, 'HL').prop_keys | \
           object.__getattribute__(self, 'HL').snip_keys | \
           object.__getattribute__(self, 'HL').interp_keys:
            return self.__getitem__(name)
        else:
            return object.__getattribute__(self, name)


class Sats(_Gal):

    def __init__(self, HL, ind, interp_ind=None):
        super().__init__(HL, ind, interp_ind=interp_ind)
        return

    def __len__(self):
        # will break if self.ind is not a bool mask
        return np.sum(self.ind)


class Host(_Gal):

    def __init__(self, HL, ind, interp_ind=None, f_sat_mask=None):
        super().__init__(HL, ind, interp_ind=interp_ind)
        if f_sat_mask is not None:
            if interp_ind is not None:
                sm, sat_inds, sat_interp_inds = f_sat_mask(self)
            else:
                sm, sat_inds = f_sat_mask(self)
                sat_interp_inds = None
            self.sats = Sats(
                HL,
                sm,
                interp_ind=sat_interp_inds,
            )
        else:
            self.sats = None
        return


class HighLev(object):

    Hydrangea_CEs = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
                     18, 21, 22, 24, 25, 28, 29)

    Lbox = 3200 * U.Mpc

    _snap_redshifts = [s.split('z')[-1].split('p') for s in suffix]
    snap_redshifts = np.array([float(s[0]) + .001 * float(s[1])
                               for s in _snap_redshifts])
    snap_scales = 1 / (1 + snap_redshifts)
    snap_times = cosmo.age(snap_redshifts)

    _units = dict(
        M200=U.Msun,
        MBH=U.Msun,
        MDM=U.Msun,
        Mgas=U.Msun,
        Mgas30kpc=U.Msun,
        Mstar=U.Msun,
        Mstar30kpc=U.Msun,
        MstarInit=U.Msun,
        Msub=U.Msun,
        R200=U.Mpc,
        SFR=U.Msun * U.yr ** -1,
        StellarHalfMassRad=U.Mpc,
        Vmax=U.km * U.s ** -1,
        VmaxRadius=U.Mpc,
        Centre=U.Mpc,
        Velocity=U.km * U.s ** -1,
        snipCoordinateDispersion=U.Mpc,
        snipCoordinates=U.Mpc,
        snipVelocity=U.km * U.s ** -1,
        snipVelocityDispersion=U.km * U.s ** -1,
        interpInterpolatedPositions=U.Mpc,
        interpInterpolationTimes=U.Gyr,
        MHI=U.Msun,
        MHneutral=U.Msun
    )

    _replacements = dict(
        M200=lambda x: _replace_invalid(x, -1, np.nan),
        MBH=lambda x: _replace_invalid(x, -1, np.nan),
        MDM=lambda x: _replace_invalid(x, -1, np.nan),
        Mgas=lambda x: _replace_invalid(x, -1, np.nan),
        Mgas30kpc=lambda x: _replace_invalid(x, -1, np.nan),
        Mstar=lambda x: _replace_invalid(x, -1, np.nan),
        Mstar30kpc=lambda x: _replace_invalid(x, -1, np.nan),
        MstarInit=lambda x: _replace_invalid(x, -1, np.nan),
        Msub=lambda x: _replace_invalid(x, -1, np.nan),
        MHI=lambda x: _replace_invalid(x, -1, np.nan),
        MHneutral=lambda x: _replace_invalid(x, -1, np.nan)
    )

    _is_log = {'M200', 'MBH', 'MDM', 'Mgas', 'Mgas30kpc', 'Mstar',
               'Mstar30kpc', 'MstarInit', 'Msub', 'SFR', 'MHI', 'MHneutral'}

    prop_keys = {'CenGal', 'ContFlag', 'M200', 'MBH', 'MDM', 'MGas',
                 'Mgas30kpc', 'Mstar', 'Mstar30kpc', 'MstarInit',
                 'Msub', 'R200', 'SFR', 'SHI', 'SatFlag',
                 'StellarHalfMassRad', 'Vmax', 'VmaxRadius', 'MHI',
                 'MHneutral'}

    pos_keys = {'Centre', 'Velocity'}

    snip_keys = {'snipCoordinateDispersion', 'snipCoordinates',
                 'snipVelocity', 'snipVelocityDispersion'}

    interp_keys = {'interpGalaxy', 'interpGalaxyRevIndex',
                   'interpInterpolatedPositions',
                   'interpInterpolationTimes'}

    def __init__(self, CE):

        self._base_dir = '/virgo/simulations/Hydrangea/10r200/CE-{:.0f}/'\
            'HYDRO/highlev/'.format(CE)
        # self._propfile = path.join(self._base_dir, 'FullGalaxyTables.hdf5')
        self._propfile = '/u/kyo/C-EAGLE_mHI/Data/CE{:02d}/'\
                         'FullGalaxyTables.hdf5'.format(CE)
        self._posfile = path.join(self._base_dir, 'GalaxyPositionsSnap.hdf5')
        self._snipfile = path.join(self._base_dir, 'GalaxyPaths.hdf5')
        self._interp_file = '/virgo/scratch/ybahe/HYDRANGEA/ANALYSIS/10r200/'\
                            'CE-{:.0f}/HYDRO/highlev/'\
                            'GalaxyCoordinates10Myr.hdf5'.format(CE)
        self._data = dict()
        return

    def _format_data(self, k, data):
        data = np.array(data)
        if k in self._replacements:
            data = self._replacements[k](data)
        if k in self._is_log:
            data = np.power(10, data)
        if k in self._units:
            data *= self._units[k]
        return data

    def _load(self, k, snipset='Basic'):
        if k in self.prop_keys:
            with h5py.File(self._propfile, 'r') as pf:
                self._data[k] = self._format_data(k, pf[k])
        elif k in self.pos_keys:
            with h5py.File(self._posfile, 'r') as pf:
                self._data[k] = self._format_data(k, pf[k])
        elif k in self.snip_keys:
            with h5py.File(self._snipfile, 'r') as pf:
                sniplist = np.array(pf['/RootIndex/'+snipset])
                parts = [np.array(
                    pf['Snepshot_{:04d}/{:s}'.format(s, k[4:])]
                )[:, np.newaxis] for s in sniplist]
                data = np.concatenate(parts, axis=1)
                self._data[k] = self._format_data(k, data)
        elif k in self.interp_keys:
            with h5py.File(self._interp_file, 'r') as pf:
                self._data[k] = self._format_data(k, pf[k[6:]])
        else:
            raise KeyError('Unknown key {:s}.'.format(k))
        if k in {'R200', 'Centre', 'StellarHalfMassRad'}:
            self._data[k] = _to_proper(self._data[k], self.snap_scales)
        elif k in {'interpInterpolatedPositions'}:
            interp_z = np.array([z_at_value(cosmo.age, t)
                                 for t in self.interpInterpolationTimes])
            self._data[k] = _to_proper(self._data[k], 1 / (1 + interp_z))

    def _load_all(self):
        for k in self.prop_keys | self.pos_keys:
            self._load(k)

    def __contains__(self, key):
        return self._data.__contains__(key)

    def __delitem__(self, key):
        return self._data.__delitem__(key)

    def __eq__(self, value):
        return self._data.__eq__(value)

    def __ge__(self, value):
        return self._data.__ge__(value)

    def __getitem__(self, key):
        if key not in self._data:
            self._load(key)
        return self._data[key]

    def __getattribute__(self, name):
        if name in object.__getattribute__(self, 'pos_keys') | \
           object.__getattribute__(self, 'prop_keys') | \
           object.__getattribute__(self, 'snip_keys') | \
           object.__getattribute__(self, 'interp_keys'):
            return self.__getitem__(name)
        else:
            return object.__getattribute__(self, name)

    def __gt__(self, value):
        return self._data.__gt__(value)

    def __iter__(self):
        return self._data.__iter__()

    def __le__(self, value):
        return self._data.__le__(value)

    def __len__(self):
        return self._data.__len__()

    def __lt__(self, value):
        return self._data.__lt__(value)

    def __ne__(self, value):
        return self._data.__ne__(value)

    def __repr__(self, value):
        return self._data.__repr__()

    def clear(self):
        return self._data.clear()

    def get(self, key, default=None):
        return self._data.get(key, default)

    def items(self):
        self._load_all()
        return self._data.items()

    def keys(self):
        return self.pos_keys | self.prop_keys

    def values(self):
        self._load_all()
        return self._data.values()
